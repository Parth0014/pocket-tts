FROM public.ecr.aws/lambda/python:3.12

WORKDIR ${LAMBDA_TASK_ROOT}

COPY requirements-lambda.txt .

# CPU-only PyTorch
RUN pip install --no-cache-dir \
    torch==2.6.0+cpu \
    --index-url https://download.pytorch.org/whl/cpu


RUN pip install --no-cache-dir \
    -r requirements-lambda.txt

COPY generate_narration.py .
COPY lambda_function.py .
COPY extractor.py .
COPY narration_script.py .
COPY chunking.py .
COPY worker_document.py .
COPY narration_content ./narration_content

CMD ["lambda_function.lambda_handler"]
