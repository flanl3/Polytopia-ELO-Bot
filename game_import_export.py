import discord
from discord.ext import commands
from pbwrap import Pastebin
from models import db, Team, Game, Player, Lineup, Tribe, Squad, SquadGame, SquadMember
from bot import config, logger, helper_roles, mod_roles
import csv
import json
import peewee

try:
    pastebin_api = config['DEFAULT']['pastebin_key']
except KeyError:
    logger.warn('pastebin_key not found in config.ini - Pastebin functionality will be limited')
    pastebin_api = None


class GameIO_Cog:
    def __init__(self, bot):
        self.bot = bot

    @commands.has_any_role(*mod_roles)
    @commands.command(aliases=['dbr'])
    async def db_restore(self, ctx):

        await ctx.send(f'Attempting to restore games from file db_import.json')
        with open('db_import.json') as json_file:
            data = json.load(json_file)
            for team in data['teams']:
                try:
                    Team.create(name=team['name'], emoji=team['emoji'], image_url=team['image'])
                except peewee.IntegrityError:
                    pass
            for tribe in data['tribes']:
                try:
                    Tribe.create(name=tribe['name'], emoji=tribe['emoji'])
                except peewee.IntegrityError:
                    pass
            for game in data['games']:
                team1, _ = Team.get_or_create(name=game['team1'][0]['team'])
                team2, _ = Team.get_or_create(name=game['team2'][0]['team'])

                newgame = Game.create(team_size=len(game['team1']), home_team=team1, away_team=team2, name=game['name'])
                team1_players, team2_players = [], []

                for p in game['team1']:
                    newplayer, _ = Player.get_or_create(discord_id=p['player_id'], defaults={'discord_name': p['player_name']})
                    newplayer.discord_name = p['player_name']
                    newplayer.polytopia_id = p['poly_id']
                    newplayer.polytopia_name = p['poly_name']
                    newplayer.save()

                    tribe_choice = p['tribe']
                    if tribe_choice is not None:
                        tribe, _ = Tribe.get_or_create(name=tribe_choice)
                    else:
                        tribe = None

                    Lineup.create(game=newgame, player=newplayer, team=team1, tribe=tribe)
                    team1_players.append(newplayer)
                    # Tribe selection would go here if I decide that should be imported

                for p in game['team2']:
                    newplayer, _ = Player.get_or_create(discord_id=p['player_id'], defaults={'discord_name': p['player_name']})
                    newplayer.discord_name = p['player_name']
                    newplayer.polytopia_id = p['poly_id']
                    newplayer.polytopia_name = p['poly_name']
                    newplayer.save()

                    tribe_choice = p['tribe']
                    if tribe_choice is not None:
                        tribe, _ = Tribe.get_or_create(name=tribe_choice)
                    else:
                        tribe = None

                    Lineup.create(game=newgame, player=newplayer, team=team2, tribe=tribe)
                    team2_players.append(newplayer)

                if len(team1_players) > 1:
                    Squad.upsert_squad(player_list=team1_players, game=newgame, team=team1)
                    Squad.upsert_squad(player_list=team2_players, game=newgame, team=team2)

                if game['winner']:
                    if team1.name == game['winner']:
                        newgame.declare_winner(winning_team=team1, losing_team=team2)
                    elif team2.name == game['winner']:
                        newgame.declare_winner(winning_team=team2, losing_team=team1)

                print(f'Creating game ID # {newgame.id} - {team1.name} vs {team2.name}')
                logger.debug(f'Creating game ID # {newgame.id} - {team1.name} vs {team2.name}')

    @commands.command(aliases=['dbb'])
    @commands.has_any_role(*mod_roles)
    async def db_backup(self, ctx):

        teams_list = []
        for team in Team.select():
            team_obj = {"name": team.name, "emoji": team.emoji, "image": team.image_url}
            teams_list.append(team_obj)

        tribes_list = []
        for tribe in Tribe.select():
            tribe_obj = {"name": tribe.name, "emoji": tribe.emoji}
            tribes_list.append(tribe_obj)

        games_list = []
        for game in Game.select():
            team1 = game.home_team
            team2 = game.away_team
            team1_players, team2_players = [], []
            for lineup in Lineup.select().join(Player).where((Lineup.game == game) & (Lineup.team == team1)):
                lineup_obj = {"player_id": lineup.player.discord_id,
                              "player_name": lineup.player.discord_name,
                              "poly_id": lineup.player.polytopia_id,
                              "poly_name": lineup.player.polytopia_name,
                              "team": lineup.team.name,
                              "tribe": lineup.tribe.name if lineup.tribe else None}
                # Could add name of tribe choice here
                team1_players.append(lineup_obj)
            for lineup in Lineup.select().join(Player).where((Lineup.game == game) & (Lineup.team == team2)):
                lineup_obj = {"player_id": lineup.player.discord_id,
                              "player_name": lineup.player.discord_name,
                              "poly_id": lineup.player.polytopia_id,
                              "poly_name": lineup.player.polytopia_name,
                              "team": lineup.team.name,
                              "tribe": lineup.tribe.name if lineup.tribe else None}
                team2_players.append(lineup_obj)
            if len(team1_players) != len(team2_players) or len(team1_players) == 0:
                # TODO: This is to just skip exporting games that have a deleted player on one side. At the moment no graceful way to handle this.
                break

            winner = game.winner.name if game.winner else None
            games_obj = {"date": str(game.date), "name": game.name, "winner": winner, "team1": team1_players, "team2": team2_players}
            games_list.append(games_obj)

        data = {"teams": teams_list, "tribes": tribes_list, "games": games_list}
        with open('db_export.json', 'w') as outfile:
            json.dump(data, outfile)

        await ctx.send('Database has been backed up to file db_export.json on my hosting server.')

    @commands.command(aliases=['gex', 'gameexport'])
    @commands.has_any_role(*helper_roles)
    @commands.cooldown(1, 300, commands.BucketType.guild)
    async def game_export(self, ctx):

        with open('games_export.csv', mode='w') as export_file:
            game_writer = csv.writer(export_file, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)

            header = ['ID', 'Winner', 'Home', 'Away', 'Date', 'Home1', 'Home2', 'Home3', 'Home4', 'Home5', 'Away1', 'Away2', 'Away3', 'Away4', 'Away5']
            game_writer.writerow(header)

            query = Game.select().where(Game.is_completed == 1)
            for q in query:
                row = [q.id, q.winner.name, q.home_team.name, q.away_team.name, str(q.date)]

                pquery = Lineup.select().where(Lineup.game == q.id)
                home_players = []
                away_players = []
                for lineup in pquery:
                    if lineup.team == q.home_team:
                        home_players.append(lineup.player.discord_name)
                    else:
                        away_players.append(lineup.player.discord_name)

                home_players.extend([''] * (5 - len(home_players)))  # Pad list of players with extra blank entries so total length is 5
                away_players.extend([''] * (5 - len(away_players)))
                row += home_players + away_players
                game_writer.writerow(row)

        pb = Pastebin(pastebin_api)
        pb_url = pb.create_paste_from_file(filepath='games_export.csv', api_paste_private=0, api_paste_expire_date='1D', api_paste_name='Polytopia Game Data')
        await ctx.send(f'Game data has been exported to the following URL: {pb_url}')

    @game_export.error
    async def game_export_handler(self, ctx, error):
        """A local Error Handler
        The global on_command_error will still be invoked after."""

        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f'This command is on cooldown. Try again in {int(error.retry_after)} seconds.')
            return
        if isinstance(error, commands.CommandInvokeError) or isinstance(error, PermissionError):
            await ctx.send(f'Error creating export file.')
            # If bot is run as a system service the export file will be created by root, which can't be over-written if bot is later run as a user
            # One fix would be to reconfigure system service to run as the user, but that is a bit complicated
            return
        await ctx.send(f'Unknown error')
        logger.warn(f'Unknown error suppressed in game_export command: {error}')
        print(error)
        # This error handler is overly simple and can't raise exceptions that it doesn't specifically handle. No way around it other than
        # writing a full error handler class.


def setup(bot):
    bot.add_cog(GameIO_Cog(bot))
