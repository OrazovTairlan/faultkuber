FROM python:3.12-slim
WORKDIR /app
COPY service.py .
ENV PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8080
CMD ["python", "service.py"]
