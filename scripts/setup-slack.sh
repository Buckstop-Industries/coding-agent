#!/usr/bin/env bash

# Fleet Agent: Slack App Automation Setup
set -e

echo "🚀 Starting Fleet Agent Slack Setup..."
echo ""

# 1. Check for Slack CLI
if ! command -v slack &> /dev/null; then
    echo "❌ Slack CLI is not installed."
    echo "Please install it first: https://api.slack.com/automation/cli/install"
    exit 1
fi

echo "✅ Slack CLI found."

# 2. Guide user to get Configuration Token
echo ""
echo "🔑 Step 1: Authentication"
echo "To create an app programmatically, you need an App Configuration Token."
echo "1. Open your browser to: https://api.slack.com/apps"
echo "2. Click 'App Configuration Tokens' in the top right."
echo "3. Generate a token for your workspace."
echo "4. Copy the token (starts with xoxe-)."
echo ""
read -p "Enter your App Configuration Token (xoxe-...): " SLACK_CONFIG_TOKEN

if [[ -z "$SLACK_CONFIG_TOKEN" ]]; then
    echo "❌ Token is required. Exiting."
    exit 1
fi

# Authenticate the CLI
echo "Authenticating Slack CLI..."
slack login --challenge "$SLACK_CONFIG_TOKEN"

# 3. Create the App
echo ""
echo "📦 Step 2: Building the App from manifest.json"
echo "Creating the Fleet Agent app in your workspace..."

# Note: The slack cli 'app create' output contains the app ID and can be parsed, 
# but for robust token extraction, we rely on the user to grab the final generated tokens 
# from the UI or via subsequent 'slack app info' commands if they are fully authenticated.
slack app create --manifest ../manifest.json

echo ""
echo "✅ App created successfully!"
echo ""
echo "🛠️ Step 3: Final Configuration"
echo "The app is now in your workspace, but you need to retrieve the runtime tokens:"
echo "1. Go to https://api.slack.com/apps and click on 'Fleet Agent'."
echo "2. Go to 'Basic Information' -> 'App-Level Tokens' to get your SLACK_APP_TOKEN (xapp-...)."
echo "3. Go to 'OAuth & Permissions' to get your SLACK_BOT_TOKEN (xoxb-...)."
echo ""
echo "Once you have them, add them to your .env file or AWS Secrets Manager:"
echo "SLACK_APP_TOKEN=xapp-..."
echo "SLACK_BOT_TOKEN=xoxb-..."
echo ""
echo "Setup complete. You can now run docker-compose up --build"
