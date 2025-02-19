export const createBlockDetector = function(blockType) {
    let buffer = '';
    let blockStarted = false;
    let blockEnded = false;

    return function processStream(input) {
        if(!blockType){
            return [false, input];
        }

        if(blockEnded) {
            return [true, null];
        }

        buffer += input;

        if (buffer.includes('```' + blockType)) {
            const startIndex = buffer.indexOf('```' + blockType);
            const endIndex = buffer.indexOf('```', startIndex + 3);

            if (endIndex !== -1) {
                // Calculate how much of the input we need
                const bufferLength = buffer.length;
                const inputStartPos = Math.max(0, bufferLength - input.length);
                const validInputPortion = buffer.substring(inputStartPos, endIndex + 3);

                // Reset buffer
                buffer = '';
                blockStarted = false;
                blockEnded = true;

                return [true, validInputPortion];
            }
        }

        return [false, input];
    };
}