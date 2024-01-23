from operator import itemgetter

from common.validate import validated
from common.llm import get_chat_llm
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate, FewShotPromptTemplate
from langchain_core.output_parsers import StrOutputParser


@validated(op="hello")
def hello(event, context, current_user, name, data):
    return {"message": invoke_doc_assistant(current_user)}


def invoke_doc_assistant(current_user):
    llm = get_chat_llm("gpt-35-turbo")

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are world class technical documentation writer that replies in one sentence."),
        ("user", "{input}")
    ])

    output_parser = StrOutputParser()

    chain = prompt | llm | output_parser

    answer = chain.invoke({"input": f"Write a technical document about how to use the Langchain API for user: {current_user}"})

    return {"message": answer}


def invoke_assistant_multi_chain():
    llm = get_chat_llm("gpt-35-turbo")

    files = [
        {"file": "file1: info about people"},
        {"file": "file2: info about cities"},
        {"file": "file3: info about countries"},
    ]

    example_prompt = PromptTemplate(
        input_variables=["file"], template="{file}"
    )

    prompt1 = FewShotPromptTemplate(
        examples=files,
        example_prompt=example_prompt,
        suffix="Question: what is the file that I should look in for {task}?",
        input_variables=["input"],
    )

    # prompt1 = ChatPromptTemplate.from_template("{files} ")
    prompt2 = ChatPromptTemplate.from_template(
        "translate '{city}' into {language}"
    )

    chain1 = prompt1 | llm | StrOutputParser()

    chain2 = (
            {"city": chain1, "language": itemgetter("language")}
            | prompt2
            | llm
            | StrOutputParser()
    )

    answer = chain2.invoke({"task": "find out about Nashville", "language": "english"})
    return {"message": answer}
