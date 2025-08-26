# Container image for running the Lambda locally or deploying as a Lambda container image
# Base image: AWS Lambda Python 3.11
FROM public.ecr.aws/lambda/python:3.11

# Copy and install Python dependencies into the Lambda task root
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt -t ${LAMBDA_TASK_ROOT}

# Copy application source code
COPY src/ ${LAMBDA_TASK_ROOT}/

# Set the Lambda handler (module.function)
CMD ["handler.lambda_handler"]
