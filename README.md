# CS340 Slack bot

This repo contains the application code for `ABSOLUTELY USELESS TWEETER BOT`, the Slack bot for CS 340. It's just a few custom slash commands, along with a SQLite DB to store the current queue.

## TODO

The Slack bot is currently a PHP server, but that is being phased out in favor of Python. So far, all logic and functionality has been moved into `slackbot.py`. The server entry point is still `index.php`, but all it does is call slackbot.py with the HTTP request data.

## Updating the list of TAs

To add or remove TAs, [access the Slack bot](#accessing-the-slack-bot-directly), then update `secrets/ta_slack_user_ids.json`. Each key is a Slack "member ID" (like U0ABCDEFG). The values (like "Dr. Wilkerson") aren't used in the code; they just tell you who has which ID.

## Accessing the Slack bot directly

To access the bot, ssh into one of the CS department lab machines, then cd into `/users/groups/cs340ta/slack`. If you don't have permission, the OIT guys (first floor of the TMCB) can give you access.

(That's effectively prod, so try to avoid developing there. See the [next section](#modifying-the-slack-bot).)

## Modifying the Slack bot

Warning: Don't commit to this repo directly on the lab machines--you'll likely run into git permissions issues. Instead, clone the repo locally and push from there, then log into the lab machines to pull your changes.

To add or remove a new slash command, you'll need to access the Slack App itself. Go to [https://api.slack.com/apps](https://api.slack.com/apps); select TWEETER BOT (ask Dr. Rodham for "Workspace Admin" privileges if you don't see it); and click "Slash Commands" on the left sidebar.

The `run_action()` function is the entry point to the slack bot's code. A slash command of the form `/cmd arg1 arg2`, sent by a user with ID `userId1234`, will trigger a call like this: `run_action("cmd", ["arg1", "arg2"], "userId1234")`.
