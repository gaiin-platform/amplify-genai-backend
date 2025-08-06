FROM public.ecr.aws/lambda/python:3.11

# Copy both requirement files
COPY requirements.txt ${LAMBDA_TASK_ROOT}
COPY agent-requirements-slim.txt ${LAMBDA_TASK_ROOT}

RUN pip install --only-binary=:all: numexpr==2.8.7
# Install dependencies from both files
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt -r ${LAMBDA_TASK_ROOT}/agent-requirements-slim.txt

# Copy your app code
COPY . ${LAMBDA_TASK_ROOT}

# Set Lambda handler
CMD ["service.core.route"]