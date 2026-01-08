# CS340 Slack bot

This repo contains the application code for `ABSOLUTELY USELESS TWEETER BOT`, the Slack bot for CS 340. It's just a few custom slash commands, along with a SQLite DB to store the current queue.

The Slack bot is currently a PHP server, but that is being phased out in favor of Python. So far, all logic and functionality has been moved into `slackbot.py`. The server entry point is still `index.php`, but all it does is call slackbot.py with the HTTP request data.

## Updating the list of TAs

To add or remove TAs, use the `/ta add @Alice` and `/ta rmv @Bob` commands. `/ta` with no arguments prints the current list of TAs.

Obviously this command requires TA privileges. If you get locked out because no one has TA access, log into the slack bot as described [below](#modifying-the-slack-bot), and copy the contents of `secrets/backup_slack_member_ids.json` into `secrets/ta_slack_member_ids.json`. This will give TA privileges to Dr. Rodham and Dr. Wilkerson, so they can add everyone else.

## Modifying the Slack bot

TweeterBot's production code is hosted on the lab machines. To access the bot directly, ssh into one of the CS department lab machines, then cd into `/users/groups/cs340ta/public_html/slack`. If you don't have permission, the OIT guys (first floor of the TMCB) can give you access. **Warning**: If you're modifying the code, don't commit to this repo directly from the lab machines--you'll likely run into git permissions issues. Instead, clone the repo locally and push from there, then log into the lab machines to pull your changes. (Editing the lab machine code is fine for experimentation, just don't commit from there.)

To add or remove a slash command, you'll need to access the Slack App itself. Go to [api.slack.com/apps](https://api.slack.com/apps); select TWEETER BOT (if you don't see it, ask Dr. Rodham for "Workspace Admin" privileges for the Slack workspace); and click "Slash Commands" on the left sidebar. When you create a new command, the Request URL should be the same as the other commands. If your command will take a user as an argument (like `/ta add @Charlie`), check the "Escape channels, users, and links" box.

### The actual code

After parsing the request data, the `run_action()` function in `slackbot.py` is the entry point to the actual command handlers.

#### Adding a new command

In the `run_action()` function, add your command to the relevant dict depending on whether it should be TA-only. The dict key is the slash-command itself (without the slash) and the value is the handler function for your command.

#### The handler function

Your handler function (ex. `handle_cmd()`) must have the same signature as the existing ones, taking in a Request and returning None. The Request you're given will contain the calling user, any command args, etc.

To access the queue, create a PersistentQueue object (all instances access the same SQLite database). For fine-grained control of permissions, like if one subcommand is TA-only and another isn't, you can call `require_ta_or_halt()` or `p.is_user_a_ta()` from inside your handler function. To post a message as TweeterBot, call send_message(), optionally with `private=False` to broadcast to the whole channel. (You can also call `send_error()` if you reach an invalid state.) If your message contains user IDs, the message string should look like `f"foo <@{user_id}> baz"` for Slack to properly @-mention them (note, that's the user_id, _not_ the user_name). Avoid calling send_message() more than once--it works, but the resulting Slack message is in JSON format for some reason.

#### The Request object

The user_id of the user who ran the command is stored in `req.requester_id`. Arguments to your command can be accessed via the `req.args` list. (That list doesn't include the actual slash command; that is stored in `req.action`. For example, `/cmd arg1 arg2` will have `req.action == "cmd"` and `req.args == ["arg1", "arg2"]`.) If an argument is meant to be a user, call `parse_arg_as_user()` on it to get a (user_id, user_name) tuple. Note that the user_id is what is used everywhere, not the user_name. (It turns out the user_name is rarely useful, but it's there in case it's needed someday.) 





