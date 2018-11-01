import discord
from discord.ext import commands
import modules.models as models
import modules.utilities as utilities
import settings
import modules.exceptions as exceptions
from modules.games import post_newgame_messaging
import peewee
import re
import datetime
import random
import logging
import asyncio

logger = logging.getLogger('polybot.' + __name__)


class PolyMatch(commands.Converter):
    async def convert(self, ctx, match_id: int):

        with models.db:
            try:
                match = models.Game.get(id=match_id)
                logger.debug(f'Match with ID {match_id} found.')

                if match.guild_id != ctx.guild.id:
                    await ctx.send(f'Match with ID {match_id} cannot be found on this server. Use {ctx.prefix}listmatches to see available matches.')
                    raise commands.UserInputError()
                return match
            except peewee.DoesNotExist:
                await ctx.send(f'Match with ID {match_id} cannot be found. Use {ctx.prefix}listmatches to see available matches.')
                raise commands.UserInputError()
            except ValueError:
                await ctx.send(f'Invalid Match ID "{match_id}".')
                raise commands.UserInputError()


class matchmaking():
    """
    Helps players find other players.
    """

    def __init__(self, bot):
        self.bot = bot
        self.bg_task = bot.loop.create_task(self.task_print_matchlist())

    # @settings.in_bot_channel()
    @settings.is_user_check()
    @commands.command(aliases=['openmatch'], usage='size expiration rules')
    async def opengame(self, ctx, *, args=None):

        """
        Opens a game that others can join
        Expiration can be between 1H - 96H
        Size examples: 1v1, 2v2, 1v1v1v1v1, 3v3v3

        **Examples:**
        `[p]opengame 1v1`
        `[p]opengame 2v2 48h`  (Expires in 48 hours)
        `[p]opengame 2v2 Large map, no bardur`  (Adds a note to the game)
        """

        team_size = False
        expiration_hours = 24
        note_args = []

        if not args:
            return await ctx.send('Game size is required. Include argument like *2v2* to specify size.'
                f'\nExample: `{ctx.prefix}opengame 1v1 large map`')

        host, _ = models.Player.get_by_discord_id(discord_id=ctx.author.id, discord_name=ctx.author.name, discord_nick=ctx.author.nick, guild_id=ctx.guild.id)
        if not host:
            # Matching guild member but no Player or DiscordMember
            return await ctx.send(f'You must be a registered player before hosting a match. Try `{ctx.prefix}setcode POLYCODE`')

        # if models.Match.select().where(
        if models.Game.select().where(
            (models.Game.host == host) & (models.Game.is_pending == 1)
        ).count() > 5:
            return await ctx.send(f'You have too many open games already. Try using `{ctx.prefix}delgame` on an existing one.')

        for arg in args.split(' '):
            m = re.fullmatch(r"\d+(?:(v|vs)\d+)+", arg.lower())
            if m:
                # arg looks like '3v3' or '1v1v1'
                team_size_str = m[0]
                team_sizes = [int(x) for x in arg.lower().split(m[1])]  # split on 'vs' or 'v'; whichever the regexp detects
                if max(team_sizes) > 6:
                    return await ctx.send(f'Invalid game size {team_size_str}: Teams cannot be larger than 6 players.')
                if sum(team_sizes) > 12:
                    return await ctx.send(f'Invalid game size {team_size_str}: Games can have a maximum of 12 players.')
                team_size = True
                continue
            m = re.match(r"(\d+)h", arg.lower())
            if m:
                # arg looks like '12h'
                if not 0 < int(m[1]) < 97:
                    return await ctx.send(f'Invalid expiration {arg}. Must be between 1H and 96H (One hour through four days).')
                expiration_hours = int(m[1])
                continue
            note_args.append(arg)

        if not team_size:
            return await ctx.send(f'Game size is required. Include argument like *2v2* to specify size')

        if sum(team_sizes) > 2 and (not settings.is_power_user(ctx)) and ctx.guild.id != settings.server_ids['polychampions']:
            return await ctx.send('You only have permissions to create 1v1 matches. More active server members can create larger matches.')

        server_size_max = settings.guild_setting(ctx.guild.id, 'max_team_size')
        if max(team_sizes) > server_size_max and ctx.guild.id != settings.server_ids['polychampions']:
            return await ctx.send(f'Maximium team size on this server is {server_size_max}.\n'
                'For full functionality with support for up to 6-person teams and team channels check out PolyChampions - <https://tinyurl.com/polychampions>')

        game_notes = ' '.join(note_args)[:100]
        notes_str = game_notes if game_notes else "\u200b"
        expiration_timestamp = (datetime.datetime.now() + datetime.timedelta(hours=expiration_hours)).strftime("%Y-%m-%d %H:%M:%S")

        with models.db.atomic():
            opengame = models.Game.create(host=host, expiration=expiration_timestamp, notes=game_notes, guild_id=ctx.guild.id, is_pending=True)
            for count, size in enumerate(team_sizes):
                models.GameSide.create(game=opengame, size=size, position=count + 1)

            models.Lineup.create(player=host, game=opengame, gameside=opengame.gamesides[0])
        await ctx.send(f'Starting new open game ID {opengame.id}. Size: {team_size_str}. Expiration: {expiration_hours} hours.\nNotes: *{notes_str}*')

    @commands.command(aliases=['matchside'], usage='match_id side_number Side Name')
    async def gameside(self, ctx, game: PolyMatch, side_lookup: str, *, args):
        """
        Give a name to a side in an open game that you host
        **Example:**
        `[p]gameside m25 2 Ronin` - Names side 2 of Match M25 as 'The Ronin'
        """

        if not game.is_pending:
            return await ctx.send(f'The game has already started and this can no longer be changed.')
        if not game.is_hosted_by(ctx.author.id)[0] and not settings.is_staff(ctx):
            return await ctx.send(f'Only the game host or server staff can do this.')

        # TODO: Have this command also allow side re-ordering
        # matchside m1 1 name ronin
        # matchside m1 ronin nelluk rickdaheals jonathan

        gameside, _ = game.get_side(lookup=side_lookup)
        if not gameside:
            return await ctx.send(f'Can\'t find that side for game {game.id}.')
        gameside.sidename = args
        gameside.save()

        return await ctx.send(f'Side {gameside.position} for game {game.id} has been named "{args}"')

    # @settings.in_bot_channel()
    @commands.command(usage='game_id', aliases=['join', 'joinmatch'])
    async def joingame(self, ctx, game: PolyMatch = None, *args):
        """
        Join an open game
        **Example:**
        `[p]joingame 25` - Join open game 25 to the first side with room
        `[p]joingame 5 ronin` - Join open game 5 to the side named 'ronin'
        `[p]joingame 5 2` - Join open game 5 to side number 2
        `[p]joingame 5 rickdaheals 2` - Add a person to a game you are hosting. Side must be specified.
        """
        if not game:
            return await ctx.send(f'No game ID provided. Use `{ctx.prefix}opengames` to list open games you can join.')
        if not game.is_pending:
            return await ctx.send(f'The game has already started and this can no longer be joined.')

        if len(args) == 0:
            # ctx.author is joining a game, no side given
            target = f'<@{ctx.author.id}>'
            side, side_open = game.first_open_side(), True
            if not side:
                return await ctx.send(f'Game {game.id} is completely full!')

        elif len(args) == 1:
            # ctx.author is joining a match, with a side specified
            target = f'<@{ctx.author.id}>'
            side, side_open = game.get_side(lookup=args[0])
            if not side:
                return await ctx.send(f'Could not find side with "{args[0]}" in game {game.id}. You can use a side number or name if available.')

        elif len(args) == 2:
            # author is putting a third party into this match
            if not settings.is_matchmaking_power_user(ctx):
                return await ctx.send('You do not have permissions to add another person to a game. Tell them to use the command:\n'
                    f'`{ctx.prefix}joingame {game.id} {args[1]}` to join themselves.')
            target = args[0]
            side, side_open = game.get_side(lookup=args[1])
            if not side:
                return await ctx.send(f'Could not find side with "{args[1]}" in game {game.id}. You can use a side number or name if available.\n'
                    f'Syntax: `{ctx.prefix}join {game.id} <player> <side>`')
        else:
            return await ctx.send(f'Invalid command. See `{ctx.prefix}help joingame` for usage examples.')

        if not side_open:
            return await ctx.send(f'That side of game {game.id} is already full. See `{ctx.prefix}game {game.id}` for details.')

        guild_matches = await utilities.get_guild_member(ctx, target)
        if len(guild_matches) > 1:
            return await ctx.send(f'There is more than one player found with name "{target}". Specify user with @Mention.')
        if len(guild_matches) == 0:
            return await ctx.send(f'Could not find \"{target}\" on this server.')

        player, _ = models.Player.get_by_discord_id(discord_id=guild_matches[0].id, discord_name=guild_matches[0].name, discord_nick=guild_matches[0].nick, guild_id=ctx.guild.id)
        if not player:
            # Matching guild member but no Player or DiscordMember
            return await ctx.send(f'"{guild_matches[0].name}" was found in the server but is not registered with me. '
                f'Players can be register themselves with `{ctx.prefix}setcode POLYTOPIA_CODE`.')

        if game.has_player(player)[0]:
            return await ctx.send(f'You are already in game {game.id}. If you are trying to change sides, use `{ctx.prefix}leave {game.id}` first.')

        models.Lineup.create(player=player, game=game, gameside=side)
        await ctx.send(f'Joining <@{player.discord_member.discord_id}> to side {side.position} of game {game.id}')

        players, capacity = game.capacity()
        if players >= capacity:
            await ctx.send(f'Game {game.id} is now full and the host <@{game.host.discord_member.discord_id}> should start the game.')
        # TODO: output correct ordering respecting side.position
        embed, content = game.embed(ctx)
        # TODO: fix embeds
        await ctx.send(embed=embed, content=content)

    @commands.command(usage='game_id', aliases=['leave', 'leavematch'])
    async def leavegame(self, ctx, game: PolyMatch):
        """
        Leave a game that you have joined

        **Example:**
        `[p]leavegame 25`
        """
        if game.is_hosted_by(ctx.author.id)[0]:

            if not settings.is_matchmaking_power_user(ctx):
                return await ctx.send('You do not have permissions to leave your own match.\n'
                    f'If you want to delete use `{ctx.prefix}deletegame {game.id}`')

            await ctx.send(f'**Warning:** You are leaving your own game. You will still be the host. '
                f'If you want to delete use `{ctx.prefix}deletegame {game.id}`')

        if not game.is_pending:
            return await ctx.send(f'Game {game.id} has already started and cannot be left.')

        lineup = game.player(discord_id=ctx.author.id)
        if not lineup:
            return await ctx.send(f'You are not a member of game {game.id}')

        lineup.delete_instance()
        await ctx.send('Removing you from the game.')

    @commands.command(usage='game_id', aliases=['notes', 'matchnotes'])
    async def gamenotes(self, ctx, game: PolyMatch, *, notes: str = None):
        """
        Edit notes for an open game you host
        **Example:**
        `[p]gamenotes 100 Large map, no bans`
        """

        if not game.is_hosted_by(ctx.author.id)[0] and not settings.is_staff(ctx):
            return await ctx.send(f'Only the game host or server staff can do this.')

        old_notes = game.notes
        if game.is_pending:
            game.notes = notes[:100] if notes else None
        else:
            # Preserve original notes and indicate they've been edited, if game is in progress
            old_notes_redacted = f'{"~~" + old_notes + "~~"} ' if old_notes else ''
            game.notes = f'{old_notes_redacted}{notes[:100]}' if notes else old_notes_redacted
        game.save()

        await ctx.send(f'Updated notes for game {game.id} to: {game.notes}\nPrevious notes were: {old_notes}')
        embed, content = game.embed(ctx)
        await ctx.send(embed=embed, content=content)

    @commands.command(usage='game_id player')
    async def kick(self, ctx, game: PolyMatch, player: str):
        """
        Kick a player from an open game
        **Example:**
        `[p]kick 25 koric`
        """
        if not game.is_hosted_by(ctx.author.id)[0] and not settings.is_staff(ctx):
            return await ctx.send(f'Only the game host or server staff can do this.')

        if not game.is_pending:
            return await ctx.send(f'Game {game.id} has already started.')

        try:
            target = models.Player.get_or_except(player_string=player, guild_id=ctx.guild.id)
        except exceptions.NoSingleMatch:
            return await ctx.send(f'Could not match "{player}" to an ELO player.')

        if target.discord_member.discord_id == ctx.author.id:
            return await ctx.send('Stop kicking yourself!')

        lineup = game.player(player=target)
        if not lineup:
            return await ctx.send(f'{target.name} is not a member of game {game.id}')

        lineup.delete_instance()
        await ctx.send(f'Removing {target.name} from the game.')

    # @settings.in_bot_channel()
    @commands.command(aliases=['listmatches', 'matchlist', 'openmatches', 'listmatch', 'match', 'matches'])
    async def opengames(self, ctx, *args):
        """
        List current open games

        Full games will still be listed until the host starts or deletes them with `[p]startgame` / `[p]deletegame`
        Add **OPEN** or **FULL** to command to filter by open/full matches
        **Example:**
        `[p]opengames` - List all unexpired open games
        `[p]opengames open` - List all open games that still have openings
        `[p]opengames waiting` - Lists open games that are full but not yet started
        """
        syntax = (f'`{ctx.prefix}opengames` - List all unexpired open games\n'
                  f'`{ctx.prefix}opengames open` - List all open games that still have openings\n'
                  f'`{ctx.prefix}opengames waiting` - Lists open games that are full but not yet started')
        models.Game.purge_expired_games()

        if len(args) > 0 and args[0].upper() == 'OPEN':
            title_str = f'Current open games with available spots'
            game_list = models.Game.select().where(
                (models.Game.id.in_(models.Game.subq_open_games_with_capacity())) & (models.Game.is_pending == 1) & (models.Game.guild_id == ctx.guild.id)
            ).order_by(-models.Game.id).prefetch(models.GameSide)

        elif len(args) > 0 and args[0].upper() == 'WAITING':
            title_str = f'Full games waiting to start'
            game_list = models.Game.waiting_to_start(guild_id=ctx.guild.id)
        elif len(args) == 0:
            title_str = f'Current open games'
            game_list = models.Game.select().where(
                (models.Game.is_pending == 1) & (models.Game.guild_id == ctx.guild.id)
            )
        else:
            return await ctx.send(f'Syntax error. Example usage:\n{syntax}')

        title_str_full = title_str + f'\nUse `{ctx.prefix}joingame #` to join one or `{ctx.prefix}game #` for more details.'
        gamelist_fields = [(f'`{"ID":<8}{"Host":<40} {"Type":<7} {"Capacity":<7} {"Exp":>4}`', '\u200b')]

        for game in game_list:

            notes_str = game.notes if game.notes else "\u200b"
            players, capacity = game.capacity()
            capacity_str = f' {players}/{capacity}'
            expiration = int((game.expiration - datetime.datetime.now()).total_seconds() / 3600.0)
            expiration = 'Exp' if expiration < 0 else f'{expiration}H'

            gamelist_fields.append((f'`{f"{game.id}":<8}{game.host.name:<40} {game.size_string():<7} {capacity_str:<7} {expiration:>5}`',
                notes_str))

        self.bot.loop.create_task(utilities.paginate(self.bot, ctx, title=title_str_full, message_list=gamelist_fields, page_start=0, page_end=15, page_size=15))
        # paginator done as a task because otherwise it will not let the waitlist message send until after pagination is complete (20+ seconds)

        waitlist = [f'{g.id}' for g in models.Game.waiting_to_start(guild_id=ctx.guild.id, host_discord_id=ctx.author.id)]
        if ctx.guild.id != settings.server_ids['polychampions']:
            await asyncio.sleep(1)
            await ctx.send('Powered by PolyChampions. League server with a focus on team play:\n'
                '<https://tinyurl.com/polychampions>')
        if waitlist:
            await asyncio.sleep(1)
            await ctx.send(f'You have full matches waiting to start: **{", ".join(waitlist)}**\n'
                f'Type `{ctx.prefix}game #` for more details.')

    # @settings.in_bot_channel()
    @commands.command(aliases=['startmatch'], usage='game_id Name of Poly Game')
    async def startgame(self, ctx, game: PolyMatch, *, name: str = None):
        """
        Start a full game and track it for ELO
        Use this command after you have created the game in Polytopia.
        **Example:**
        `[p]startgame 100 Fields of Fire`
        """

        if not game.is_hosted_by(ctx.author.id)[0] and not settings.is_staff(ctx):
            return await ctx.send(f'Only the match host or server staff can do this.')

        if not name:
            return await ctx.send(f'Game name is required. Example: `{ctx.prefix}startgame {game.id} Name of Game`')

        if not game.is_pending:
            return await ctx.send(f'Game {game.id} has already started with name **{game.name}**')

        players, capacity = game.capacity()
        if players != capacity:
            return await ctx.send(f'Game {game.id} is not full.\nCapacity {players}/{capacity}.')

        sides, mentions = [], []

        for side in game.gamesides:
            # TODO: This won't necessarily respect side ordering
            current_side = []
            for gameplayer in side.lineup:
                guild_member = ctx.guild.get_member(gameplayer.player.discord_member.discord_id)
                if not guild_member:
                    return await ctx.send(f'Player *{gameplayer.player.name}* not found on this server. (Maybe they left?)')
                current_side.append(guild_member)
                mentions.append(guild_member.mention)
            sides.append(current_side)

        teams_for_each_discord_member, list_of_final_teams = models.Game.pregame_check(discord_groups=sides,
                                                                guild_id=ctx.guild.id,
                                                                require_teams=settings.guild_setting(ctx.guild.id, 'require_teams'))

        with models.db.atomic():
            # Convert game from pending matchmaking session to in-progress game
            for team_group, allied_team, side in zip(teams_for_each_discord_member, list_of_final_teams, game.gamesides):
                side_players = []
                for team, lineup in zip(team_group, side.lineup):
                    lineup.team = team
                    lineup.save()
                    side_players.append(lineup.player)

                if len(side_players) > 1:
                    squad = models.Squad.upsert(player_list=side_players, guild_id=ctx.guild.id)
                    side.squad = squad

                side.team = allied_team
                side.save()

            game.is_pending = False
            game.save()

        logger.info(f'Game {game.id} closed and being tracked for ELO')
        await post_newgame_messaging(ctx, game=game)

    @commands.command(aliases=['rtribes', 'rtribe'], usage='game_size [-banned_tribe ...]')
    async def random_tribes(self, ctx, size='1v1', *args):
        """Show a random tribe combination for a given game size.
        This tries to keep the sides roughly equal in power.
        **Example:**
        `[p]rtribes 2v2` - Shows Ai-mo/Imperius & Xin-xi/Luxidoor
        `[p]rtribes 2v2 -hoodrick -aquarion` - Remove Hoodrick and Aquarion from the random pool. This could cause problems if lots of tribes are removed.
        """

        m = re.match(r"(\d+)v(\d+)", size.lower())
        if m:
            # arg looks like '3v3'
            if int(m[1]) != int(m[2]):
                return await ctx.send(f'Invalid match format {size}. Sides must be equal.')
            if not 0 < int(m[1]) < 7:
                return await ctx.send(f'Invalid match size {size}. Accepts 1v1 through 6v6')
            team_size = int(m[1])
        else:
            team_size = 1
            args = list(args) + [size]
            # Handle case of no size argument, but with tribe bans

        tribes = [
            ('Bardur', 1),
            ('Kickoo', 1),
            ('Luxidoor', 1),
            ('Imperius', 1),
            ('Elyrion', 2),
            ('Zebasi', 2),
            ('Hoodrick', 2),
            ('Aquarion', 2),
            ('Oumaji', 3),
            ('Quetzali', 3),
            ('Vengir', 3),
            ('Ai-mo', 3),
            ('Xin-xi', 3)
        ]
        for arg in args:
            # Remove tribes from tribe list. This could cause problems if too many tribes are removed.
            if arg[0] != '-':
                continue
            removal = next(t for t in tribes if t[0].upper() == arg[1:].upper())
            tribes.remove(removal)

        team_home, team_away = [], []

        tribe_groups = {}
        for tribe, group in tribes:
            tribe_groups.setdefault(group, set()).add(tribe)

        available_tribe_groups = list(tribe_groups.values())
        for _ in range(team_size):
            available_tribe_groups = [tg for tg in available_tribe_groups if len(tg) >= 2]

            this_tribe_group = random.choice(available_tribe_groups)

            new_home, new_away = random.sample(this_tribe_group, 2)
            this_tribe_group.remove(new_home)
            this_tribe_group.remove(new_away)

            team_home.append(new_home)
            team_away.append(new_away)

        await ctx.send(f'Home Team: {" / ".join(team_home)}\nAway Team: {" / ".join(team_away)}')

    async def task_print_matchlist(self):

        await self.bot.wait_until_ready()
        challenge_channels = [g.get_channel(settings.guild_setting(g.id, 'match_challenge_channel')) for g in self.bot.guilds]
        while not self.bot.is_closed():
            await asyncio.sleep(60 * 60)  # delay before and after loop so bot wont spam if its being restarted several times
            for chan in challenge_channels:
                if not chan:
                    continue

                models.Game.purge_expired_games()
                game_list = models.Game.select().where(
                    (models.Game.id.in_(models.Game.subq_open_games_with_capacity())) & (models.Game.is_pending == 1) & (models.Game.guild_id == chan.guild.id)
                ).order_by(-models.Game.id).prefetch(models.GameSide)[:12]
                if not game_list:
                    continue

                pfx = settings.guild_setting(chan.guild.id, 'command_prefix')
                embed = discord.Embed(title='Recent open games\n'
                    f'Use `{pfx}joingame #` to join one or `{pfx}game #` for more details.')
                embed.add_field(name=f'`{"ID":<8}{"Host":<40} {"Type":<7} {"Capacity":<7} {"Exp":>4}`', value='\u200b', inline=False)
                for game in game_list:

                    notes_str = game.notes if game.notes else "\u200b"
                    players, capacity = game.capacity()
                    capacity_str = f' {players}/{capacity}'
                    expiration = int((game.expiration - datetime.datetime.now()).total_seconds() / 3600.0)
                    expiration = 'Exp' if expiration < 0 else f'{expiration}H'

                    embed.add_field(name=f'`{game.id:<8}{game.host.name:<40} {game.size_string():<7} {capacity_str:<7} {expiration:>5}`', value=notes_str)

                await chan.send(embed=embed)

            await asyncio.sleep(60 * 60)


def setup(bot):
    bot.add_cog(matchmaking(bot))
