# Built-ins and 3rd party modules
from datetime import datetime, timedelta
from os import environ as envVars
from threading import Lock
import discord
import asyncio

# Local modules
import fs
import utils
import errors
import dblAPI
import spacexAPI
import staticMessages
import embedGenerators
from discordUtils import safeSend, safeSendLaunchInfo

# TODO: Replace print statements with propper logging
# TODO: Remove a channel from localData if it causes an InvalidArgument error (doesn't exist anymore)

config = fs.loadConfig()

"""
Constants / important variables - See config/README.md
"""
PREFIX = config["commandPrefix"]
API_CHECK_INTERVAL = config["apiCheckInterval"]
LAUNCH_NOTIF_DELTA = timedelta(minutes = config["launchNotificationDelta"])

"""
localData is a dictionary that has a lock (as it is accessed a lot in multiple functions) and is used
to store multiple things:
 - A list of channel IDs that are subscribed
 - The latest launch information embed that was sent
 - Whether or not an active launch notification has been sent for the current launch
This is saved to and loaded from a file (so it persists through reboots/updates)
"""
localData = fs.loadLocalData()
localDataLock = Lock()  # locks access when saving / loading

discordToken = utils.loadEnvVar("SpaceXLaunchBotToken")
client = discord.Client()

async def notificationBackgroundTask():
    """
    Every $API_CHECK_INTERVAL minutes:
    If the embed has changed, something new has happened so send
        all channels an embed with updated info
    If the time of the next upcoming launch is within the next hour,
        send out a notification embed alerting people
    """
    await client.wait_until_ready()
    while not client.is_closed:
        nextLaunchJSON = await spacexAPI.getNextLaunchJSON()
        if nextLaunchJSON == 0:
            pass  # Error, do nothing, wait for 30 more mins
        
        else:
            launchInfoEmbed, launchInfoEmbedLite = await embedGenerators.getLaunchInfoEmbed(nextLaunchJSON)
            
            with localDataLock:
                if localData["latestLaunchInfoEmbed"].to_dict() == launchInfoEmbed.to_dict():
                    pass
                else:
                    # Launch info has changed, set variables
                    localData["launchNotifSent"] = False
                    localData["latestLaunchInfoEmbed"] = launchInfoEmbed

                    # new launch found, send all "subscribed" channel the embed
                    for channelID in localData["subscribedChannels"]:
                        channel = client.get_channel(channelID)
                        await safeSendLaunchInfo(client, channel, [launchInfoEmbed, launchInfoEmbedLite])

            launchTime = nextLaunchJSON["launch_date_unix"]
            if await utils.isInt(launchTime):

                # Get timestamp for the time $LAUNCH_NOTIF_DELTA minutes from now
                nextHour = (datetime.utcnow() + LAUNCH_NOTIF_DELTA).timestamp()

                # If the launch time is within the next hour
                if nextHour > int(launchTime):

                        with localDataLock:
                            if localData["launchNotifSent"] == False:
                                localData["launchNotifSent"] = True

                                notifEmbed = await embedGenerators.getLaunchNotifEmbed(nextLaunchJSON)
                                for channelID in localData["subscribedChannels"]:
                                    channel = client.get_channel(channelID)
                                    await safeSend(client, channel, embed=notifEmbed)

        with localDataLock:
            await fs.saveLocalData(localData)

        await asyncio.sleep(60 * API_CHECK_INTERVAL)

@client.event
async def on_message(message):
    if message.author.bot:
        # Don't reply to bots (includes self)
        return

    try:
        userIsAdmin = message.author.permissions_in(message.channel).administrator
    except AttributeError:
        # Happens if user has no roles
        userIsAdmin = False

    # Commands can be in any case
    message.content = message.content.lower()
    
    if message.content.startswith(PREFIX + "nextlaunch"):
        # TODO: Maybe just pull latest embed from localData instead of requesting every time?
        nextLaunchJSON = await spacexAPI.getNextLaunchJSON()
        if nextLaunchJSON == 0:
            launchInfoEmbed, launchInfoEmbedLite = errors.apiErrorEmbed, errors.apiErrorEmbed
        else:
            launchInfoEmbed, launchInfoEmbedLite = await embedGenerators.getLaunchInfoEmbed(nextLaunchJSON)
        await safeSendLaunchInfo(client, message.channel, [launchInfoEmbed, launchInfoEmbedLite])

    elif userIsAdmin and message.content.startswith(PREFIX + "addchannel"):
        # Add channel ID to subbed channels
        replyMsg = "This channel has been added to the launch notification service"
        with localDataLock:
            if message.channel.id not in localData["subscribedChannels"]:
                localData["subscribedChannels"].append(message.channel.id)
                await fs.saveLocalData(localData)
            else:
                replyMsg = "This channel is already subscribed to the launch notification service"
        await safeSend(client, message.channel, text=replyMsg)
    
    elif userIsAdmin and message.content.startswith(PREFIX + "removechannel"):
        # Remove channel ID from subbed channels
        replyMsg = "This channel has been removed from the launch notification service"
        with localDataLock:
            try:
                localData["subscribedChannels"].remove(message.channel.id)
                await fs.saveLocalData(localData)
            except ValueError:
                replyMsg = "This channel was not previously subscribed to the launch notification service"
        await safeSend(client, message.channel, text=replyMsg)

    elif message.content.startswith(PREFIX + "info"):
        await safeSend(client, message.channel, embed=staticMessages.infoEmbed)
    elif message.content.startswith(PREFIX + "help"):
        await safeSend(client, message.channel, embed=staticMessages.helpEmbed)

@client.event
async def on_ready():
    global dbl  # Can't define this until client is ready
    dbl = dblAPI.dblClient(client)

    await client.change_presence(game=discord.Game(name="with Elon"))

    with localDataLock:
        totalSubbed = len(localData["subscribedChannels"])
    totalServers = len(client.servers)
    totalClients = 0
    for server in client.servers:
        totalClients += len(server.members)

    print("\nLogged into Discord API\n")
    print("Username: {}\nClientID: {}\n\nConnected to {} servers\nConnected to {} subscribed channels\nServing {} clients".format(
        client.user.name,
        client.user.id,
        totalServers,
        totalSubbed,
        totalClients
    ))
    await dbl.updateServerCount(totalServers)

@client.event
async def on_server_join(server):
    await dbl.updateServerCount(len(client.servers))

@client.event
async def on_server_remove(server):
    await dbl.updateServerCount(len(client.servers))

client.loop.create_task(notificationBackgroundTask())
client.run(discordToken)
