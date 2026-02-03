# Telegram Support Bot - Natural Chat

## Was macht der Bot?

Du chattest mit Kunden über eine Telegram-Gruppe, aber für den Kunden sieht es aus wie ein normaler Chat mit einer Person.

**Für den Kunden:**
- Normale Telegram-Konversation
- Sprachnachrichten, Bilder, alles wie gewohnt
- Merkt nicht, dass ein Bot dazwischen ist

**Für dich:**
- Alle Chats zentral in einer Gruppe
- Jeder Kunde = ein Topic
- Inbox mit allen ungelesenen
- Chats als ungelesen markieren

---

## Setup (5 Minuten)

### 1. Bot erstellen
1. [@BotFather](https://t.me/BotFather) → `/newbot`
2. Token kopieren

### 2. Support-Gruppe
1. Neue Gruppe erstellen
2. → Supergruppe umwandeln
3. → Forum-Topics aktivieren
4. Bot als Admin hinzufügen
5. Gruppen-ID holen ([@userinfobot](https://t.me/userinfobot) zur Gruppe hinzufügen)

### 3. Konfigurieren
In `bot.py`:
```python
BOT_TOKEN = "dein-token"
SUPPORT_GROUP_ID = -100xxxxxxxxxx
ADMIN_IDS = [deine-user-id]
```

### 4. Starten
```bash
pip install python-telegram-bot
python bot.py
```

---

## Befehle

### Inbox
| Befehl | Beschreibung |
|--------|--------------|
| `/inbox` | Alle ungelesenen |
| `/all` | Alle Chats |
| `/search <text>` | Suchen |

### Im Topic
| Befehl | Beschreibung |
|--------|--------------|
| `/unread` | Als ungelesen markieren |
| `/read` | Als gelesen markieren |
| `/info` | User-Info |
| `/note <text>` | Notiz hinzufügen |
| `/vip` | VIP toggle |
| `/urgent` | Urgent toggle |
| `/close` | Archivieren |
| `/t <name>` | Template senden |

---

## Status-Emojis

| Emoji | Bedeutung |
|-------|-----------|
| 🔴 | Ungelesen |
| ⚪ | Gelesen |
| 🟢 | Beantwortet |
| ⭐ | VIP |
| 🚨 | Urgent |

---

## Templates anpassen

In `bot.py`:
```python
TEMPLATES = {
    "hi": "Hey! 👋 Wie kann ich dir helfen?",
    "danke": "Gerne! Bei Fragen melde dich 😊",
    # Weitere hinzufügen...
}
```

---

## Server-Betrieb

```bash
# Mit screen
screen -S bot
python bot.py
# Ctrl+A, D

# Oder systemd
sudo nano /etc/systemd/system/support-bot.service
```

```ini
[Unit]
Description=Support Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/pfad/zum/bot
ExecStart=/usr/bin/python3 bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable support-bot
sudo systemctl start support-bot
```
