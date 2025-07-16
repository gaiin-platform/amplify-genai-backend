from .process_input_schema import process_input_schema
from .dual_retrieval_schema import dual_retrieval_schema
from .terminate_embedding_schema import terminate_embedding_schema
from .embedding_ids_schema import embedding_ids_schema

rules = {
    "validators": {
        "/embedding-dual-retrieval": {"dual-retrieval": dual_retrieval_schema},
        "/embedding-retrieval": {"retrieval": process_input_schema},
        "/embedding/terminate": {"terminate": terminate_embedding_schema},
        "/embedding/sqs/get": {"get": {}},
        "/embedding-delete": {"embedding-delete": embedding_ids_schema},
        "/embedding/check-completion": {"embeddings-check": embedding_ids_schema},
    },
    "api_validators": {
        "/embedding-dual-retrieval": {"dual-retrieval": dual_retrieval_schema},
        "/embedding-delete": {"embedding-delete": embedding_ids_schema},
        "/embedding/check-completion": {"embeddings-check": embedding_ids_schema},
    },
}
