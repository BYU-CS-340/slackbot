# CS340 Slack bot

TweeterBot manages online and in-person passoff queues for CS340. `index.php` verifies incoming Slack requests and forwards them to `slackbot.py`. Queue data is stored in `db/queue.sqlite`.

## Student commands

| Command | Action |
| --- | --- |
| `/passoff-online` | Join the online queue. |
| `/passoff-inperson` | Choose whether group passoff is OK in a popup, then join the in-person queue. |
| `/passoff` | Show the two join commands. |
| `/wait` | Show queue sizes and your position. |
| `/queue` | List both queues. |
| `/nevermind` | Leave your queue. |

Students can join only one queue at a time. In-person queue order starts when the form is submitted; canceling does not join. The form requires an explicit Yes or No and confirms the join privately. To switch queues or change your preference, leave and rejoin, which resets your place. Positions count people ahead, starting at zero.

## TA commands

| Command | Action |
| --- | --- |
| `/next` | Call the longest-waiting student across both queues. |
| `/next-online` | Call the next online student and ask them to DM the TA a Zoom link. |
| `/next-inperson` | Call the next in-person student individually. |
| `/next-group <number>` | Call up to that many consenting in-person students together. |
| `/nevermind @student` | Remove a student from either queue. |
| `/clearqueue` | Clear both queues and announce who was removed. |
| `/closequeue` | Announce closing and clear both queues. New joins remain enabled. |
| `/ta` | List TAs. |
| `/ta add @student` | Grant TA access. |
| `/ta rmv @student` | Remove TA access. |
| `/bottest` | Show the parsed command, arguments, and requester ID. |

### Group consent and fairness

`/next-group 3` calls and removes up to three students who agreed to group passoff. The number must be a whole number from 1 to 5. Values outside that range and decimals are rejected without changing the queue. The online queue is unaffected.

Groups never skip someone who requested an individual passoff. Selection stops before that student, even if it makes a smaller group. When they reach the front, `/next-group` removes nobody and tells the TA to use `/next-inperson` before calling another group. This prevents later arrivals from repeatedly overtaking them; TAs must serve individual students to keep the queue moving.

## Slack configuration

Configure these settings before deploying:

1. Set `SLACK_BOT_TOKEN` to the app's bot OAuth token in the server environment inherited by PHP and Python. It is used to open the consent form.
2. Set `SLACK_SIGNING_SECRET` to the app's Signing Secret in the PHP server environment. **The endpoint rejects all requests if this is missing.** Keep both secrets out of the repository.
3. In the [Slack app settings](https://api.slack.com/apps), select TWEETER BOT and register the commands listed above under **Slash Commands**, using the same Request URL pointing to `index.php`. Register the group command as `/next-group` with usage hint `<number>`; remove any old `/next_group` registration. Enable **Escape channels, users, and links** for commands that accept user mentions.
4. Enable **Interactivity & Shortcuts** and set its Request URL to the same endpoint so the bot receives form submissions.

If the app is not visible, ask Dr. Rodham for workspace admin access. The popup uses Slack's [`views.open`](https://docs.slack.dev/reference/methods/views.open/) API and [modal submission callbacks](https://docs.slack.dev/surfaces/modals/).

## Deployment and data

Production is hosted on the CS lab machines at `/users/groups/cs340ta/public_html/slack`. SSH into a lab machine to access it. Contact OIT on the first floor of the TMCB if you need filesystem permissions.

Commit and push from a local checkout, then pull changes on the server. Avoid committing from the lab machines because of repository permissions.

The server needs PHP, Python 3.10 or newer with SQLite support, and write access to both `db/` and its database file. No third-party Python packages are required. Back up the database before deployment. Schema updates run automatically, preserving queue order. Legacy rows without a queue type become in-person entries; existing students without recorded consent default to individual-only passoff.

TA access is stored in `secrets/ta_slack_member_ids.json`. If nobody has TA access, restore it from `secrets/backup_slack_member_ids.json` on the server, then use `/ta add` to grant access as needed.

## Development

`run()` routes slash commands to `run_action()` and modal submissions to `handle_interaction()`. To add a command, register its name without the leading slash in the student or TA-only handler dictionary. Handlers accept a `Request` and return `None`.

The request exposes `action`, `args`, `requester_id`, and `trigger_id`. Use `PersistentQueue` for database access and `parse_arg_as_user()` for user mentions. Queue selection and removal happen in a single transaction to prevent concurrent TAs from calling the same student.

Use `send_message()` for a private command response or `send_message(..., private=False)` for a channel announcement. Mention users as `<@USER_ID>`. Emit only one JSON response per request; modal submissions use Slack's response-action format.
