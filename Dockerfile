FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Default: produce both firms' reports and run all checks.
CMD ["python", "run.py", "--both"]
