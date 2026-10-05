#!/bin/bash
set -e

# Configuration
FUNCTION_NAME="aetherion-sbox-voice-webhook"
REGION="us-west-1"
PROFILE="aetherion"

echo "📦 Creating deployment package..."

# Create a temporary directory for the deployment package
TEMP_DIR=$(mktemp -d)
echo "Using temporary directory: $TEMP_DIR"

# Copy Lambda function code
cp *.py "$TEMP_DIR/"

# Install dependencies to temporary directory
echo "📥 Installing dependencies..."
uv pip install --python-platform linux --python 3.12 --target "$TEMP_DIR/" --quiet --requirements pyproject.toml

# Create ZIP file
cd "$TEMP_DIR"
ZIP_FILE="lambda-deployment.zip"
echo "🗜️  Creating ZIP file..."
zip -r "$ZIP_FILE" . -q

# Deploy to AWS Lambda
echo "🚀 Deploying to AWS Lambda: $FUNCTION_NAME"
aws lambda update-function-code \
    --function-name "$FUNCTION_NAME" \
    --zip-file "fileb://$ZIP_FILE" \
    --region "$REGION" \
    --profile "$PROFILE"

# Clean up
echo "🧹 Cleaning up..."
rm -rf "$TEMP_DIR"

echo "✅ Deployment complete!"

