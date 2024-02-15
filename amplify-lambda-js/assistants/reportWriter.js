import {
    AssistantState, chainActions,
    DoneState,
    HintState, invokeAction, llmAction, mapKeysAction, outputAction, outputToResponse,
    PromptAction, PromptForDataAction,
    reduceKeysAction,
    StateBasedAssistant, updateStatus
} from "./statemachine/states.js";


const writeSection =
    new PromptAction("Write a section about: {{arg}}",
        "sectionOfReport");

const writeSections = mapKeysAction(
    outputToResponse(
        chainActions([
            updateStatus("writeSection", {summary: "Writing Section: {{arg}}", inProgress: true}),
            writeSection,
            updateStatus("writeSection", {summary: "Done writing.", inProgress: false}),
        ]),
        "## {{arg}}"
    ),
    "section",
    null,
    "sectionOfReport"
)

const combineSections = reduceKeysAction(
    invokeAction((sections)=>sections.join("\n\n"), ["arg"], "report"),
"section",
    "report",
    "report");

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
            },
            (m) => {
                return m.section1 && m.section2;
            }
        ),
    ),
    writeSections: new AssistantState("writeSections",
        "Writing the sections",
        writeSections
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

