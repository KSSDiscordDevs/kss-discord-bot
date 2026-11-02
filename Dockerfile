FROM python:3.11-slim

WORKDIR /app

# Copy dependency list and install packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your project files
COPY . .

# Run your Discord bot main entrypoint
CMD ["python", "main.py"]