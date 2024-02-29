

export const anthropicTransform = (event) => {
    if(event && event.d && event.d.completion) {
        return {d: event.d.completion}
    }
    else {
        return null;
    }
}
