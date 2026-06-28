FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir flask gunicorn
COPY probe.py .
EXPOSE 8080
# 4 workers × 1 thread is plenty for a stateless probe service; each
# request blocks at most 6 seconds (TCP + UDP timeouts in series).
CMD ["gunicorn", "-b", "0.0.0.0:8080", "-w", "4", "--timeout", "30", "probe:app"]
