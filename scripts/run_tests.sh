#!/bin/bash
# Run MCP Gateway tests and save results to test_results.txt

cd "$(dirname "$0")/.."

echo "============================================" > test_results.txt
echo "MCP Gateway Test Results" >> test_results.txt
echo "Date: $(date)" >> test_results.txt
echo "============================================" >> test_results.txt

# Create venv if it doesn't exist and install pytest
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate
pip install pytest -q

# Run pytest and capture output
pytest tests/test_gateway.py -v --tb=short 2>&1 | tee test_results.txt

echo "" >> test_results.txt
echo "============================================" >> test_results.txt

# Check for failures
if grep -q "FAILED\|ERROR" test_results.txt; then
    echo "❌ TESTS FAILED - Please review the output above" >> test_results.txt
    exit 1
else
    echo "✅ All tests passed successfully!" >> test_results.txt
    exit 0
fi
