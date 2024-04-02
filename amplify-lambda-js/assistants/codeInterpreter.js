import {
    AssistantState, chainActions,
    DoneState, USER_INPUT_STATE, UserInputState,outputToStatus, invokeAction, 
    PromptAction, StateBasedAssistant, updateStatus
} from "./statemachine/states.js";
import {sendDeltaToStream} from "../common/streams.js";
import {getLogger} from "../common/logging.js";

const logger = getLogger("codeInterpreterAssitant");
const PROVIDER = process.env.ASSISTANTS_OPENAI_PROVIDER;
const description = `This assistants  executes Python in a secure sandbox, handling diverse data to craft files and visual graphs.
It tackles complex code and math challenges through iterative problem-solving, refining failed attempts into successful executions.
Use this for complex mathmatical operations and coding tasks that involve the need to run the code in a sandbox environment.`;

const additionalPrompt = `You have access to a sandboxed environment for writing and testing code. When appropriate, please provide code. You should follow these steps tp ensure you produce excellent code samples 
                            1. Write the code.
                            2. Anytime you write new code display a preview of the code to show your work.
                            3. Run the code to confirm that it runs.
                            4. If the code is unsuccessful, try to revise the code and rerun going through the steps from above again.
                            5. If the code is successful show us the input data and output of your code in Markdown
                            Always display code blocks in mark down. comment the code with explanations of what the code is doing for any complex sections of code. 
                            Do not include download links or mock download links! Instead tell me some information about the images you have produced and refer to them by their corresponding file name. Feel feel to give some explanation to the users messsage and tie it back in with the created files.` 


const requestErrorResponse = (context) => {
    sendDeltaToStream(context.responseStream, "codeInterpreter", formatResponse( { success: false, error: 'Internal error when making code interpreter request.'} ))

}

//function call to code_interpeter endpoints in an action type format
const invokeCodeIterpreterAction = 
    // { execute: 
    async (llm, context, dataSources) => { 

        if (!(context)) return; 
        const token = context.params.account.accessToken;
        const options = context.body.options
        // if we have a codeInterpreterId then we can chat if we dont then we have to create first 

        // The conversation currently does have an assistantID in our database (which contains an assistantID with code_interpreter)
        let assistantId = options.codeInterpreterAssistantId || null;
       /*
        if we have a conversation assistant id and it for some reason doesnt work we need to create a new assistant
        then in the front end update the conversations code interpreter assistant id
        */
            // The conversation currently does not have an assistantID in our database 
        if (assistantId === null) {
            
            const create_data = {
                access_token: token,
                name: "CodeInterpreter",
                description: description, 
                tags: [],
                instructions:  options.prompt + additionalPrompt,
                dataSources: context.data.dataSources || [], // see which has all the datasources in the entire conversation 
                tools: [{"type": "code_interpreter"}], 
                provider: PROVIDER // can be 'azure' or 'openai'
            }

            try {
                const responseData = await fetchRequest(token, create_data, process.env.ASSISTANTS_CREATE_CODE_INTERPRETER_ENPOINT); 

                if (responseData && responseData.success) {
                    assistantId = responseData.data.assistantId
                    //we need to ensure we send the assistant_id back to be saved in the conversation
                    sendDeltaToStream(context.responseStream, "codeInterpreter", `codeInterpreterAssistantId=${assistantId}`);
                    

                } else {
                    logger.debug(`Error with creating an assistant: ${responseData}`)
                }
            } catch (error) {
                // This will catch any errors thrown by fetchRequest or the fetch operation itself
                console.error('Fetch Request to create assistant Failed:', error);
                requestErrorResponse(context);
            }
        }
        //ensure that assistant_id is not null (in case assistant creation was necessary and failed)
        if (assistantId) {
            context.body.messages.at(-1)['content'] += "\nDo not include mock download links! Do generate images when asked. If applicable, Do tell me some information about the file you have produced and refer to them by their corresponding file name. "
            const chat_data = {
                id: assistantId,
                messages: context.body.messages.slice(1),
                fileKeys: context.data.conversationDataSources  || []//context.activeDataSources
            }

            try {
                const responseData = await fetchRequest(token, chat_data, getInterpreterUrl());
                logger.debug(`Response data from chatting with code interpreter: ${responseData}`)

                if (responseData.success && responseData.data) {
                    sendDeltaToStream(context.responseStream, "codeInterpreter", formatResponse(responseData));
                } else {
                    requestErrorResponse(context);
                }
            } catch (error) {
                // This will catch any errors thrown by fetchRequest or the fetch operation itself
                console.error('Fetch Request to chat with assistant Failed:', error);
                requestErrorResponse(context);

            }
        } 
    
        await new Promise(resolve => setTimeout(resolve, 3000)); // give stream time to get to user
        // to context.data that will guide the llm in choosing the states by determining whether code interpreter is done or not
        context.data['isCodeInterpreterDone'] = true;

    }
// }



function getInterpreterUrl() {
    // note: i see in assistant provider is included, may come from there
    if (PROVIDER === 'openai') {
        return process.env.ASSISTANTS_OPENAI_CODE_INTERPRETER_ENDPOINT
    } else if (PROVIDER === 'azure') {
        return process.env.ASSISTANTS_AZURE_CODE_INTERPRETER_ENDPOINT
    } else {
        throw new Error('Invalid provider specified.');
    }
}

async function fetchRequest(token, data, url) {
    try {
        const response = await fetch(url, {
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${token}`  //check on this, we may already have it 
                },
                method: 'POST',
                body: JSON.stringify({'data': data})
            }); 
            if (!response.ok) {
                throw new Error('Network response error');
            }
            return await response.json();
    } catch (error) {
        console.error('Error invoking Code interpreter Lambda: ', error);
    }
}

const codeInterpreterStatusUpdate = 
    { execute: 
        (llm, context, dataSources) => {
            let updateSummary
            if (context?.data?.isCodeInterpreterDone) {
                updateSummary = "Finalizing code interpreter results...";
            } else {
                updateSummary = "Code interpreter is making progress on your request...";
            } 

            context.data['codeInterpreterCurStatusSummary'] = updateSummary
        }
    }


function formatResponse(data) {
    return `codeInterpreterResponseData=${JSON.stringify(data)}`
}


const todaysDate = () => {
    const today = new Date();
    return `${today.toLocaleDateString('en-US', { month: 'long', day: 'numeric' })}`; 
}

//give user time to read
const sleepAction = { 
    execute: async (llm, context, dataSources) => {
        await new Promise(resolve => setTimeout(resolve, 13000)); 
    }
  }

  function formatCamelToSentence(str) {
    //takes in "todayInHistory" returns "Today In History"
    return str.split(/(?=[A-Z])/).map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()).join(' ')
  }


  const riddleAnswerActions = 
        [ { execute: (llm, context, dataSources) => {
                const answerPromptAction =  
                new PromptAction(
                    [{ role: "user",
                        content: `What is the answer to this riddle? ${context.data.guessTheRiddle}. Response with the answer only and an exclamation point! Feel free to add some laughs`
                    }], 'riddleAnswer', { appendMessages: false, streamResults: false, retries: 1, isEntertainment: true }
                );
                return answerPromptAction.execute(llm, context, dataSources);
            }
        }, updateStatus('riddleAnswer', {inProgress: true}, 'riddleAnswer'), 
        { execute: async (llm, context, dataSources) => {  await new Promise(resolve => setTimeout(resolve, 3000)) }}];


  function createEntertainmentAction(actionType, description, content, appendMessages = false) {
    return new AssistantState(actionType, description,
            outputToStatus(
                { summary: `While you wait, enjoy a dose of entertainment with ${formatCamelToSentence(actionType)}!`, inProgress: true },
                chainActions([
                    new PromptAction(
                        [{  role: "user",
                            content: `I want entertainment! The topic is "${actionType}". 
                            This is a array of previous responses you have said. Do not repeat anything similar. ${entertainmentHistoryList} 
                            \n\n Do not respond to anything else except the following text, without any introduction or preamble: ${content}\n\n`
                        }], actionType, { appendMessages: appendMessages, streamResults: false, retries: 1, isEntertainment: true}
                    ),
                    updateStatus(actionType, {inProgress: true}, actionType), sleepAction, 
                    ...(actionType == 'guessTheRiddle' ? riddleAnswerActions : []),
                    (llm, context, dataSources) => { States.randomEntertainment.removeTransitions();
                        context.data['entertainmentHistory'][actionType].push(context.data[actionType]); }
                ])
            )
    );
}

function entertainmentHistoryList (actionType) { /////
    return (llm, context, dataSources) => {
        return  String(context.data['entertainmentHistory'][actionType]);
    }
}

const selectEntertainment = (llm, context, dataSources) => {
    if (context.data['isCodeInterpreterDone']) {
        States.randomEntertainment.addTransition(States.done.name, "Code Interpreter is done so we are done");
    } else {
        const leftEntertainment = context.data['entertainmentTypes'].length;
        if (leftEntertainment === 0) context.data['entertainmentTypes'] = ['todayInHistory', 'onTopicPun', 'roastMyPrompt','guessTheRiddle', 'lifeHacks'];
        const randomIndex = Math.floor(Math.random() * (leftEntertainment - 1));
        
        const entertainmentSelected = context.data['entertainmentTypes'][randomIndex];
        switch (entertainmentSelected) {
            case 'todayInHistory':
                States.randomEntertainment.addTransition(States.todayInHistory.name, "The next random state is today in history, go here");
                break;
            case 'onTopicPun':
                States.randomEntertainment.addTransition(States.onTopicPun.name, "The next random state is says puns, go here");
                break;
            case 'roastMyPrompt':
                States.randomEntertainment.addTransition(States.roastMyPrompt.name, "The next random state is prompt roasting, go here");
                break;
            case 'guessTheRiddle':
                States.randomEntertainment.addTransition(States.guessTheRiddle.name, "The next random state is riddles, go here");
                break;
            case 'lifeHacks':
                States.randomEntertainment.addTransition(States.lifeHacks.name, "The next random state is  life hacks, go here");
                break;
            default:
                console.log("Error with entertainment states")
        }
        context.data['entertainmentTypes'].splice(randomIndex, 1);
    }
}

// This is the set of states that will be in the state machine.
const States = {
    initialState: new AssistantState("initialState",
    "Update user and set up for Code Interpreter",
    chainActions([
        updateStatus("prepareCodeInterpreter", {summary: "Preparing your request to code interpreter...", inProgress: true}),
       
        (llm, context, dataSources) => { context.data['isCodeInterpreterDone'] = false; // add a flag to context.data that will guide the llm in choosing the states by determining whether code interpreter is done or not
            let entertainmentTypes = ['todayInHistory', 'onTopicPun', 'roastMyPrompt','guessTheRiddle', 'lifeHacks'];
            let entertainmentHistory = {};
            entertainmentTypes.forEach((type) => {
                entertainmentHistory[type] = [];
            });
            context.data['entertainmentTypes'] = entertainmentTypes;
            context.data['entertainmentHistory'] = entertainmentHistory;
            context.data['userPrompt'] = context.body.messages.at(-1)['content'];
        }, 
    ]), false,
    ),

    invokeCodeInterpreter: new AssistantState("invokeCodeInterpreter",
    "Calling Code Interpreter", invokeCodeIterpreterAction, false, {}, true
    ),

    chooseEntertainment: new AssistantState("chooseEntertainment",
        "Randomly choose the next entertainment state while we wait for code interpreter! Pick from 'Today in History', 'On Topic Pun', 'Roast My Prompt', 'Guess the Riddle', or 'Life Hacks' ",
        null,
        false, {extraInstructions: {postInstructions: "Choose a random state to go to"}}
    ),
    //randomize state calls [entertianment] random index .add  call function and remove it,  
    
    randomEntertainment: new AssistantState("randomEntertainment",
        "Determine if we need more entertainment to wait for code interpreter to finish up",
        chainActions([selectEntertainment, codeInterpreterStatusUpdate, 
            updateStatus("codeInterpreterStatus", {inProgress: true}, 'codeInterpreterCurStatusSummary'), 
        ]), 
    ),

    todayInHistory: createEntertainmentAction("todayInHistory",
                "Respond with a fun, short, and interesting Today in History fact",
                `Using historical knowledge up to the current date, provide a fun, short, and interesting "Today in History" fact related to today's month and day (${todaysDate()}). 
                Can you give me 3 concise facts about events that happened on this day in history, in any year? 
                Each fact should be two or three sentences and appropriate for a general audience.`,
                    
    ),

    onTopicPun: createEntertainmentAction("onTopicPun",
                "Respond with creative fun and silly puns based on the theme of the conversation",
                `Generate 3 puns related to this conversation's overall topic or theme, ensuring they're clever and 
                suitable for all audiences. The puns should be engaging, sparking a light-hearted moment for the reader.`, true
    ),
    

    roastMyPrompt: createEntertainmentAction("roastMyPrompt",
                "Look at the user input prompts and give them a roast", 
                `Craft 1 concise light-hearted roast about the users previous 'user' prompt messages (outline which user message you are referring to), keeping it witty and suitable for all 
                audiences. The roast should playfully critique the prompt's theme or content in one or two sentences while 
                being informative while suggesting tips to enhance the users prompt crafting skills.`, true
    ),
    

    guessTheRiddle: createEntertainmentAction("guessTheRiddle",
                "Respond with a moderately easy riddle to the user",
                `Compose a moderately easy riddle that appeals to a wide audience, ensuring it's engaging yet simple enough to solve. 
                The riddle should be crafted in one or two sentences, designed to give that 'aha!' moment to anyone who figures it out.`
    ),
    
    lifeHacks: createEntertainmentAction("lifeHacks",
                "Respond with practical and simple life hacks",
                `Provide 3 life hacks that simplify common daily tasks, ensuring they're practical and easy to implement for a 
                wide audience. Each hack should be explained in one or two sentences, designed to offer a quick and effective solution.
                The life hacks can also relate to the general topic and tools discussed in the conversation but, not required`, true
    ),
    

    iRatherWait:  new AssistantState("iRatherWait",
        "Just wait for code interpreter to finish up",
        chainActions([ sleepAction, codeInterpreterStatusUpdate,
            updateStatus("invokeCodeInterpreter", {inProgress: true}, 'codeInterpreterCurStatusSummary'),
        ]),
        false, {extraInstructions: {postInstructions: 
                `Is Code Interpreter done? If not, we need to continue to wait until it is done.
                If it is done processing then go to the done state.`}}
    ),
    // This is the end state.
    done: new DoneState(),
};

// // We start in the outline state.
const current = States.initialState;

// We add transitions to the state machine to define the state machine.
States.initialState.addTransition(States.invokeCodeInterpreter.name, "Start by calling Code Interpreter");

States.invokeCodeInterpreter.addTransition(States.randomEntertainment.name, "Choose a random next state!");

States.todayInHistory.addTransition(States.randomEntertainment.name, "Go Check if we need more entertainment");
States.onTopicPun.addTransition(States.randomEntertainment.name, "Go Check if we need more entertainment");
States.roastMyPrompt.addTransition(States.randomEntertainment.name, "Go Check if we need more entertainment");
States.guessTheRiddle.addTransition(States.randomEntertainment.name, "Go Check if we need more entertainment");
States.lifeHacks.addTransition(States.randomEntertainment.name, "Go Check if we need more entertainment");


// We create the assistant with the state machine and the current state.
export const codeInterpreterAssistant = new StateBasedAssistant(
    "Code Interpreter Assistant",
    "Code Interpreter Assistant",
    description, 
    //This function should return true if the assistant can support the dataSources and false otherwise.
    (m) => {
        return true
    },
    (m) => {
        return true
    },
    // This is the state machine that the assistant will use to process requests.
    States,
    // This is the current state that the assistant will start in.
    current
);

