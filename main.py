import asyncio
from pyrogram import Client, filters, enums,idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions
from pymongo import AsyncMongoClient
import re, os
from pyrogram.errors import FloodWait, UserIsBlocked, PeerIdInvalid, MessageNotModified
from pyrogram.enums import ChatMemberStatus
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)

api_id = os.getenv("API_ID", 0)
api_hash = os.getenv("API_HASH", "81719734c6a0af15e5d35006655c1f84")
bot_token = os.getenv("BOT_TOKEN", "8181075654:AAF_UqJxLYDp-odK8-SM-PK8WoTS_yX98cc")
mongodb_uri = os.getenv(
    "MONGO_DB_URI",
    "mongodb+srv://Editguardian:Shiv@cluster0.bznqliz.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0",
)
support_gc = os.getenv("SUPPORT_GROUP", "")
support_ch = os.getenv("SUPPORT_CHANNEL", "")
owner = int(os.getenv("OWNER_ID", "7706682472"))

app = Client("my_bot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)

mongo_client = AsyncMongoClient(mongodb_uri)
db = mongo_client["bio_filter_bot"]
settings_col = db["settings"]
warnings_col = db["warnings"]
approved_users_col = db["approved_users"]
chatsdb = db["chats"]
usersdb = db["chatsdb"]

url_pattern = re.compile(
    r"(https?://|www\.)[a-zA-Z0-9.\-]+(\.[a-zA-Z]{2,})+(/[a-zA-Z0-9._%+-]*)*"
)
mention_pattern = re.compile(r"@[ऀ-ॿa-zA-Z0-9_]{5,}")

cache = {
    "users": [],
    "chats": [],
}
is_broadcasting = False
# --- USERS ---


async def get_served_users() -> list:
    if not cache["users"]:
        async for user in usersdb.find({"user_id": {"$gt": 0}}):
            cache["users"].append(user["user_id"])
    return cache["users"]


async def add_served_user(user_id: int):
    await get_served_users()
    if user_id in cache["users"]:
        return
    await usersdb.insert_one({"user_id": user_id})
    cache["users"].append(user_id)


# --- CHATS ---


async def get_served_chats() -> list:
    if not cache["chats"]:
        async for chat in chatsdb.find({"chat_id": {"$lt": 0}}):
            cache["chats"].append(chat["chat_id"])
    return cache["chats"]


async def add_served_chat(chat_id: int):
    await get_served_chats()
    if chat_id in cache["chats"]:
        return
    await chatsdb.insert_one({"chat_id": chat_id})
    cache["chats"].append(chat_id)


async def get_settings(chat_id):
    setting = await settings_col.find_one({"chat_id": chat_id})
    return setting or {"chat_id": chat_id, "warn_limit": 3, "action": "mute"}


async def set_settings(chat_id, warn_limit, action):
    await settings_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"warn_limit": warn_limit, "action": action}},
        upsert=True,
    )


async def get_warnings(user_id):
    doc = await warnings_col.find_one({"user_id": user_id})
    return doc["count"] if doc else 0


async def add_warning(user_id):
    await warnings_col.update_one(
        {"user_id": user_id},
        {"$inc": {"count": 1}},
        upsert=True,
    )


async def clear_warning(user_id):
    await warnings_col.delete_one({"user_id": user_id})


async def is_approved(user_id, chat_id):
    doc = await approved_users_col.find_one({"user_id": user_id, "chat_id": chat_id})
    return doc is not None


async def approve_user(user_id, chat_id):
    await approved_users_col.update_one(
        {"user_id": user_id, "chat_id": chat_id},
        {"$set": {"approved": True}},
        upsert=True,
    )


async def unapprove_user(user_id, chat_id):
    await approved_users_col.delete_one({"user_id": user_id, "chat_id": chat_id})

async def is_admin(client, chat_id, user_id):
    async for member in client.get_chat_members(
        chat_id, filter=enums.ChatMembersFilter.ADMINISTRATORS
    ):
        if member.user.id == user_id:
            return True
    return False


@app.on_message(filters.group & filters.command("config"))
async def configure(client, message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not await is_admin(client, chat_id, user_id):
        return await message.reply_text(
            "<b>❌ You are not administrator</b>", parse_mode=enums.ParseMode.HTML
        )

    current = await get_settings(chat_id)
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Warn", callback_data="warn")],
            [
                InlineKeyboardButton(
                    "Mute ✅" if current["action"] == "mute" else "Mute",
                    callback_data="mute",
                ),
                InlineKeyboardButton(
                    "Ban ✅" if current["action"] == "ban" else "Ban",
                    callback_data="ban",
                ),
            ],
            [InlineKeyboardButton("Close", callback_data="close")],
        ]
    )
    await message.reply_text(
        "<b>Select punishment for users who have links or usernameremove?? in their bio:</b>",
        reply_markup=keyboard,
        parse_mode=enums.ParseMode.HTML,
    )


keyboard = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton("🚀 𝗨𝗽𝗱𝗮𝘁𝗲", url=support_gc),
            InlineKeyboardButton("💬 𝗦𝘂𝗽𝗽𝗼𝗿𝘁", url=support_ch),
        ],
    ]
)


@app.on_callback_query()
async def callback_handler(client, cq):
    data = cq.data
    chat_id = cq.message.chat.id
    user_id = cq.from_user.id

    if not await is_admin(client, chat_id, user_id):
        return await cq.answer("❌ You are not administrator", show_alert=True)

    current = await get_settings(chat_id)

    if data == "close":
        return await cq.message.delete()

    elif data == "back":
        return await configure(client, cq.message)

    elif data == "warn":
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "3 ✅" if current["warn_limit"] == 3 else "3",
                        callback_data="warn_3",
                    ),
                    InlineKeyboardButton(
                        "4 ✅" if current["warn_limit"] == 4 else "4",
                        callback_data="warn_4",
                    ),
                    InlineKeyboardButton(
                        "5 ✅" if current["warn_limit"] == 5 else "5",
                        callback_data="warn_5",
                    ),
                ],
                [
                    InlineKeyboardButton("Back", callback_data="back"),
                    InlineKeyboardButton("Close", callback_data="close"),
                ],
            ]
        )
        return await cq.message.edit_text(
            "<b>Select the number of warnings before punishment:</b>",
            reply_markup=keyboard,
            parse_mode=enums.ParseMode.HTML,
        )

    elif data in ["mute", "ban"]:
        await set_settings(chat_id, current["warn_limit"], data)
        return await configure(client, cq.message)

    elif data.startswith("warn_"):
        limit = int(data.split("_")[1])
        await set_settings(chat_id, limit, current["action"])
        return await configure(client, cq.message)

    elif data.startswith("unmute_"):
        uid = int(data.split("_")[1])
        try:
            await client.restrict_chat_member(
                chat_id, uid, ChatPermissions(can_send_messages=True)
            )
            await cq.message.edit(
                f"<b>User <code>{uid}</code> has been unmuted</b>",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            await cq.message.edit("I don't have permission to unmute users.")

    elif data.startswith("unban_"):
        uid = int(data.split("_")[1])
        try:
            await client.unban_chat_member(chat_id, uid)
            await cq.message.edit(
                f"<b>User <code>{uid}</code> has been unbanned</b>",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            await cq.message.edit("I don't have permission to unban users.")

    await cq.answer()


@app.on_message(filters.group & filters.command("approve"))
async def approve_user_command(client, message):
    chat_id = message.chat.id
    from_user_id = message.from_user.id

    if not await is_admin(client, chat_id, from_user_id):
        return await message.reply_text(
            "<b>❌ You are not an administrator</b>", parse_mode=enums.ParseMode.HTML
        )

    target_user = None

    if message.reply_to_message:
        target_user = message.reply_to_message.from_user
    elif len(message.command) > 1:
        arg = message.command[1]
        if arg.isdigit():
            try:
                target_user = await client.get_users(int(arg))
            except Exception:
                return await message.reply_text("❌ Invalid user ID.")
        else:
            if arg.startswith("@"):
                arg = arg[1:]
            try:
                target_user = await client.get_users(arg)
            except Exception:
                return await message.reply_text("❌ Invalid username.")
    else:
        return await message.reply_text(
            "❌ Please reply to a message or provide a username/user ID."
        )

    if await is_approved(target_user.id, chat_id):
        return await message.reply_text(
            "❌ This user is already approved in this group."
        )

    await approve_user(target_user.id, chat_id)
    await message.reply_text(
        f"✅ User {target_user.mention} has been approved for this group."
    )


@app.on_message(filters.group & filters.command("unapprove"))
async def unapprove_user_command(client, message):
    chat_id = message.chat.id
    from_user_id = message.from_user.id

    if not await is_admin(client, chat_id, from_user_id):
        return await message.reply_text(
            "<b>❌ You are not an administrator</b>", parse_mode=enums.ParseMode.HTML
        )

    target_user = None

    if message.reply_to_message:
        target_user = message.reply_to_message.from_user
    elif len(message.command) > 1:
        arg = message.command[1]
        if arg.isdigit():
            try:
                target_user = await client.get_users(int(arg))
            except Exception:
                return await message.reply_text("❌ Invalid user ID.")
        else:
            if arg.startswith("@"):
                arg = arg[1:]
            try:
                target_user = await client.get_users(arg)
            except Exception:
                return await message.reply_text("❌ Invalid username.")
    else:
        return await message.reply_text(
            "❌ Please reply to a message or provide a username/user ID."
        )

    if not await is_approved(target_user.id, chat_id):
        return await message.reply_text("❌ This user is not approved in this group.")

    await unapprove_user(target_user.id, chat_id)
    await message.reply_text(
        f"❌ User {target_user.mention} has been unapproved from this group."
    )


@app.on_message(filters.group & filters.command("approvelist"))
async def approvelist_command(client, message):
    chat_id = message.chat.id
    user_id_admin_check = message.from_user.id

    if not await is_admin(client, chat_id, user_id_admin_check):
        return await message.reply_text(
            "<b>❌ You are not an administrator</b>", parse_mode=enums.ParseMode.HTML
        )

    approved_users = approved_users_col.find({"chat_id": chat_id})
    text = ""
    async for user_doc in approved_users:
        try:
            user = await client.get_users(user_doc["user_id"])
            text += f"• <code>{user.id}</code> | {user.first_name} (@{user.username or 'N/A'})\n"
        except Exception:
            continue

    if not text:
        return await message.reply_text("❌ No users have been approved in this group.")

    await message.reply_text(
        f"✅ Approved Users in this group:\n\n{text}", parse_mode=enums.ParseMode.HTML
    )


@app.on_message(filters.command("stats") & filters.user(owner))
async def stats(client, message):
    x = len(await get_served_chats())
    y = len(await get_served_users())

    await message.reply(f"Total Chats: {x}\nTotal users: {y}")


@app.on_message(
    filters.command(["gcast", "broadcast", "gcastpin", "broadcastpin"])
    & filters.user(owner)
)
async def gcast_command(client, message):
    global is_broadcasting
    if is_broadcasting:
        return await message.reply_text("⚠️ A broadcast is already in progress.")

    is_broadcasting = True
    chats = await get_served_chats()
    users = await get_served_users()
    targets = list(set(chats + users))

    pin = message.command[0].endswith("pin")

    if message.reply_to_message:
        msg = message.reply_to_message
    elif len(message.command) > 1:
        msg_text = message.text.split(None, 1)[1]
        msg = None
    else:
        is_broadcasting = False
        return await message.reply_text(
            "❌ Provide text or reply to a message to broadcast."
        )

    panel = await message.reply_text("📣 Broadcasting Message...")

    success = 0
    failed = 0

    for i, chat_id in enumerate(targets):
        try:
            if msg:
                sent = await msg.copy(chat_id)
            else:
                sent = await client.send_message(chat_id, msg_text)
            if pin:
                try:
                    await sent.pin(disable_notification=False)
                except Exception:
                    pass

            success += 1
        except FloodWait as e:
            try:
                await panel.edit(f"⏸️ Sleeping for {e.value} seconds due to FloodWait.")
            except Exception:
                pass
            await asyncio.sleep(e.value)
        except (UserIsBlocked, PeerIdInvalid, MessageNotModified):
            failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.1)

    await panel.edit(
        f"📢 Broadcast Complete\n✅ Success: {success}\n❌ Failed: {failed}"
    )
    is_broadcasting = False


@app.on_message(filters.command("start"))
async def start_com(client, message):
    x = await client.get_me()
    start_buttons = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "➕ Add me to your Group",
                    url=f"https://t.me/{x.username}?startgroup=true",
                )
            ],
            [
                InlineKeyboardButton("🚀 𝗨𝗽𝗱𝗮𝘁𝗲", url=support_gc),
                InlineKeyboardButton("💬 𝗦𝘂𝗽𝗽𝗼𝗿𝘁", url=support_ch),
            ],
        ]
    )

    help_text = (
        "<b>👋 Hello! I'm a Bio Filter Bot.</b>\n\n"
        "I help protect your group from users with suspicious bios (URLs or usernames).\n\n"
        "<b>🔧 Commands:</b>\n"
        "• <code>/approve</code> - Approve a user (reply to their message or use ID)\n"
        "• <code>/unapprove</code> - Revoke approval\n"
        "• <code>/approvelist</code> - List all approved users\n"
        "• <code>/config</code> - Set warnings & punishment\n"
        "• <code>/stats</code> - Show usage stats (owner only)\n"
        "• <code>/gcast</code> or <code>/broadcast</code> - Broadcast a message to all users/groups\n"
        "• <code>/gcastpin</code> or <code>/broadcastpin</code> - Broadcast and pin the message\n\n"
        "Add me to your group and make me admin to get started!"
    )
    await add_served_user(message.from_user.id)
    await message.reply_text(
        help_text, reply_markup=start_buttons, parse_mode=enums.ParseMode.HTML
    )


@app.on_message(filters.group)
async def check_bio(client, message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    x=  await app.get_chat_member(message.chat.id, message.from_user.id)
    if x.status in  [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
        return 
    await add_served_chat(chat_id)
    sp = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🚀 𝗨𝗽𝗱𝗮𝘁𝗲", url=support_gc),
                InlineKeyboardButton("💬 𝗦𝘂𝗽𝗽𝗼𝗿𝘁", url=support_ch),
            ],
        ]
    )

    if await is_approved(user_id, chat_id):
        return
    try:
        user_full = await client.get_chat(user_id)
    except Exception:
        return

    bio = user_full.bio or ""
    username = f"@{user_full.username}" if user_full.username else user_full.first_name

    if re.search(url_pattern, bio) or re.search(mention_pattern, bio):
        try:
            await message.delete()
        except Exception:
            return await message.reply_text("❌ Grant me delete message permissions.")

        current = await get_settings(chat_id)
        warn_count = await get_warnings(user_id) + 1
        await add_warning(user_id)

        text = f"🚨 {username}, your message was deleted because your bio contains a link.n\nWarning {warn_count}/{current['warn_limit']}"
        reply = await message.reply_text(text, reply_markup=sp)

        if warn_count >= current["warn_limit"]:
            try:
                if current["action"] == "mute":
                    await client.restrict_chat_member(
                        chat_id, user_id, ChatPermissions()
                    )
                    kb = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "Unmute ✅", callback_data=f"unmute_{user_id}"
                                )
                            ]
                        ]
                    )
                    await reply.edit(f"{username} has been 🔇 muted.", reply_markup=kb)
                elif current["action"] == "ban":
                    await client.ban_chat_member(chat_id, user_id)
                    kb = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "Unban ✅", callback_data=f"unban_{user_id}"
                                )
                            ]
                        ]
                    )
                    await reply.edit(f"{username} has been 🔨 banned.", reply_markup=kb)
            except Exception:
                await reply.edit(
                    f"I don't have permission to {current['action']} users."
                )
            await clear_warning(user_id)

logging.getLogger(__name__).info("starting....")

async def main():
    await get_served_chats()
    await get_served_users()
    await app.start()
    await idle()

loop = asyncio.get_event_loop()
loop.run_until_complete(main())