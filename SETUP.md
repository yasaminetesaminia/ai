# Setup Guide — WhatsApp AI Receptionist

## 1. Install Dependencies

```bash
pip install -r requirements.txt
```

## 2. WhatsApp Business API Setup

1. Go to [Meta Developer Portal](https://developers.facebook.com/)
2. Create a new App → Select "Business" type
3. Add "WhatsApp" product to your app
4. In WhatsApp → Getting Started:
   - Copy your **Temporary Access Token** → put in `.env` as `WHATSAPP_TOKEN`
   - Copy your **Phone Number ID** → put in `.env` as `WHATSAPP_PHONE_NUMBER_ID`
5. Set a custom **Verify Token** (any string you choose) → put in `.env` as `WHATSAPP_VERIFY_TOKEN`
6. For **Webhook URL**: you need a public URL (see step 5 below for ngrok)

## 3. Google Cloud Setup

### Create a Service Account:
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use existing)
3. Enable these APIs:
   - **Google Calendar API**
   - **Google Sheets API**
4. Go to **IAM & Admin → Service Accounts** → Create Service Account
5. Download the JSON key file → save as `credentials.json` in project root

### Google Calendar:
1. Open [Google Calendar](https://calendar.google.com/)
2. Create a new calendar for your clinic (or use existing)
3. Go to Calendar Settings → Share with the service account email (from step 3)
4. Copy the **Calendar ID** → put in `.env` as `GOOGLE_CALENDAR_ID`

### Google Sheets:
1. Create a new Google Sheet
2. Share it with the service account email (give Editor access)
3. Copy the **Sheet ID** from the URL: `https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit`
4. Put it in `.env` as `GOOGLE_SHEETS_ID`

## 4. API Keys

### Anthropic (Claude):
1. Go to [Anthropic Console](https://console.anthropic.com/)
2. Create an API key → put in `.env` as `ANTHROPIC_API_KEY`

### OpenAI (Whisper — for voice messages):
1. Go to [OpenAI Platform](https://platform.openai.com/)
2. Create an API key → put in `.env` as `OPENAI_API_KEY`

## 5. Create .env File

```bash
cp .env.example .env
```

Fill in all values in `.env`.

## 6. Run Locally with ngrok

### Install ngrok:
Download from [ngrok.com](https://ngrok.com/download) and install.

### Start the app:
```bash
python app.py
```

### Start ngrok (in another terminal):
```bash
ngrok http 5000
```

### Configure WhatsApp Webhook:
1. Copy the ngrok HTTPS URL (e.g., `https://abc123.ngrok.io`)
2. In Meta Developer Portal → WhatsApp → Configuration:
   - **Callback URL**: `https://abc123.ngrok.io/webhook`
   - **Verify Token**: same as `WHATSAPP_VERIFY_TOKEN` in `.env`
   - **Subscribe** to: `messages`

## 7. Business Configuration

Edit these in `.env`:

| Variable | Description | Example |
|----------|-------------|---------|
| `BUSINESS_NAME` | Clinic name | `کلینیک دکتر احمدی` |
| `BUSINESS_SERVICES` | Comma-separated services | `مشاوره,معاینه,نوبت دکتر` |
| `BUSINESS_WORKING_HOURS_START` | Opening time | `09:00` |
| `BUSINESS_WORKING_HOURS_END` | Closing time | `18:00` |
| `BUSINESS_APPOINTMENT_DURATION_MINUTES` | Slot duration | `30` |
| `BUSINESS_TIMEZONE` | Timezone | `Asia/Tehran` |

## 8. Test It

1. Send a WhatsApp message to your business number
2. The bot should greet you and ask for your name
3. Follow the conversation flow to book an appointment
4. Check Google Calendar for the event
5. Check Google Sheets for the client record

## Production Deployment

For production, replace ngrok with a proper server:
- Deploy to a cloud server (AWS, DigitalOcean, etc.)
- Use a reverse proxy (Nginx)
- Get a permanent Meta access token (System User Token)
- Use HTTPS with a real domain
