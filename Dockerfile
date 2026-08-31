FROM public.ecr.aws/lambda/python:3.11

WORKDIR ${LAMBDA_TASK_ROOT}

COPY requirements-lambda.txt .

RUN pip install --no-cache-dir -r requirements-lambda.txt

COPY generate_narration.py .
COPY extractor.py .
COPY narration_script.py .
COPY chunking.py .
COPY lambda_function.py .

# Lambda's handler is lambda_function.lambda_handler
CMD ["lambda_function.lambda_handler"]
