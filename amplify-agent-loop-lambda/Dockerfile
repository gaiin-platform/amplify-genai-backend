FROM public.ecr.aws/lambda/python:3.11

COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install --no-cache-dir -r requirements.txt pandas numpy matplotlib
COPY . ${LAMBDA_TASK_ROOT}

CMD ["service.core.route"]
