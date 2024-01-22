from common.secrets import get_secret_value
from common.validate import validated
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

def get_openai_key():
    openai_api_key = get_secret_value("OPENAI_API_KEY")
    return openai_api_key


@validated(op="hello")
def hello(event, context, current_user, name, data):

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are world class technical documentation writer."),
        ("user", "{input}")
    ])

    llm = ChatOpenAI(openai_api_key=get_openai_key())
    output_parser = StrOutputParser()

    chain = prompt | llm | output_parser

    answer = chain.invoke({"input": f"Write a technical document about how to use the Langchain API for user: {current_user}"})

    return {"message": answer}
