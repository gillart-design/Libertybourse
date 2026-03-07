#!/usr/bin/env bash
set -euo pipefail

mkdir -p data output .streamlit

cat > .streamlit/config.toml <<'EOF'
[server]
headless = true
enableCORS = false
enableXsrfProtection = false

[browser]
gatherUsageStats = false

[theme]
base = "light"
primaryColor = "#123874"
backgroundColor = "#edf3ff"
secondaryBackgroundColor = "#dff0ff"
textColor = "#102a5c"
EOF

export PORTFOLIO_AUTH_MODE="${PORTFOLIO_AUTH_MODE:-optional}"

exec python -m streamlit run portfolio_simulator_app.py \
  --server.address=0.0.0.0 \
  --server.port="${PORT:-8501}" \
  --server.headless=true \
  --browser.gatherUsageStats=false
