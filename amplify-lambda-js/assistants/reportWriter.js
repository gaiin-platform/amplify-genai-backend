import {
    AssistantState, chainActions,
    DoneState,
    HintState, invokeAction, llmAction, mapKeysAction, outputAction,
    PromptAction, PromptForDataAction,
    reduceKeysAction,
    StateBasedAssistant
} from "./statemachine/states.js";


const writeSection =
    new PromptAction("Write a section about: {{arg}}",
        "sectionOfReport");

const writeSections = mapKeysAction(
    writeSection,
    "section",
    null,
    "sectionOfReport"
)

const combineSections = reduceKeysAction(
    invokeAction((sections)=>sections.join("\n\n"), ["arg"], "report"),
"section",
    "report",
    "report");

const writeReportAction = chainActions([
    writeSections,
    combineSections,
    outputAction("The report is:\n{{report}}")
    ]
);

const States = {
    outline: new AssistantState("outline",
        "Creating an outline",
        new PromptForDataAction("Write an outline for the report and output each section on a new line with the section prefix as shown",
            {
                "section1":"the first section",
                "section2":"the second section",
                "section3":"the third section",
                "section4":"the fourth section",
                "section5":"the fifth section"
            }
        ),
    ),
    writeSections: new AssistantState("writeSections",
        "Writing the sections",
        writeReportAction
    ),
    done: new DoneState(),
};

const current = States.outline;

States.outline.addTransition(States.writeSections.name, "Write the Report");
States.writeSections.addTransition(States.done.name, "Done");

export const reportWriterAssistant = new StateBasedAssistant(
    "Report Writer Assistant",
    "Report Writer Assistant",
    "This assistant creates an outline and then drafts a report that is longer than can be " +
    "written in a single prompt. It is designed to help you write a report.",
    (m) => {return true},
    (m) => {return true},
    States,
    current
);

