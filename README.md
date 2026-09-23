# Devices export

Streamlit app that pages through picker-backend devices and downloads them as CSV. A go-live table shows which stores have a device online, sleeping, or not online.

## Run

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

Set `APP_PASSWORD` and `ACCOUNT_ID` in `.env`, then sign in. Paste a Deliverect access token to export. `ZAPIER_WEBHOOK_URL` is optional.
