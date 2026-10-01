ChronoRoom — Voice Chat Telegram Bot

A Telegram bot for anonymous voice conversations with random partners, voice effects, and an interactive mini-game.

📸 Demo

"ChronoRoom Bot Demo" (screenshots/demo.jpg)

✨ Features

- 🎤 Random partner search
- 🔊 Anonymous voice communication
- 🤖 Robot voice effect
- 👹 Demon voice effect
- 🐿️ Chipmunk voice effect
- 🎲 Random voice effect
- 🎮 "Guess the Word" mini-game
- 🔄 Switch to another partner
- 👤 Optional username exchange
- 📊 User and game statistics
- 💾 SQLite database
- 🔐 Telegram channel subscription check

🔄 How It Works

1. The user opens the bot and completes the subscription check.
2. The bot searches for another available user.
3. When a partner is found, both users can exchange voice messages.
4. Voice messages can be processed with a selected voice effect before being sent to the partner.
5. Users can switch to another partner at any time.
6. A mini-game can be started during a conversation.
7. In "Guess the Word", one player explains a randomly selected word using voice while the other tries to guess it.
8. Players can optionally exchange their Telegram usernames.

🎮 Mini-Game

Guess the Word

- One player receives a secret word.
- The player explains the word using a voice message.
- The other player guesses the word using text.
- After a correct answer, the players switch roles.
- A new word is selected for the next round.
- The game keeps track of the score.

🔊 Voice Processing

Voice messages can be processed using FFmpeg with several audio effects:

- Robot
- Demon
- Chipmunk
- Random effect

If voice processing fails, the bot can send the original voice message instead.

🧩 Project Structure

ChronoRoom-Bot3/
├── README.md
├── screenshots/
│   └── demo.jpg
├── bot.py
├── config.py
└── requirements.txt

Files

- "bot.py" — main bot logic, handlers, matchmaking, voice processing and mini-game
- "config.py" — bot configuration and feature settings
- "requirements.txt" — Python dependencies

🛠 Tech Stack

- Python 3
- pyTelegramBotAPI
- SQLite
- Telegram Bot API
- FFmpeg
- threading
- queue

💾 Data Storage

SQLite is used for user statistics and game session records.

Temporary voice files are processed locally and can be removed automatically after sending.

🔒 Configuration

The Telegram bot token and other private settings should be stored outside the source code.

FFmpeg must also be installed on the system running the bot.

🚀 Running the Bot

Install the Python dependencies:

pip install -r requirements.txt

Install FFmpeg and configure the required settings in "config.py".

Then run:

python bot.py

📌 About

ChronoRoom-Bot3 is one of several independent Telegram bots created for the ChronoRoom project.
