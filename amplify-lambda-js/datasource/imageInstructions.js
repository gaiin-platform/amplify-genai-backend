// Prompt text that tells the model which images accompany a message.
// Kept free of AWS/S3 imports so it can be unit tested on its own.

export const additionalImageInstruction = (imageFileNames) => {
    // Build image reference labels to help model map filenames to images
    const imageReferenceLabels = imageFileNames
        .map((filename, idx) => `- Image #${idx + 1}: ${filename}`)
        .join('\n');

    return `Image Reference Labels:\n${imageReferenceLabels}\n\nWhen describing or referencing images, use the image numbers (#1, #2, etc.) and filenames shown above.\n\n Additional Image Instructions:\n If given an encode image, describe the image in vivid detail, capturing every element, including the subjects, colors, textures, and emotions. Provide enough information so that someone can visualize the image perfectly without seeing it, using precise and rich language. This should be its own block of text.`
}

// Images the user attached that we could not load (e.g. the S3 object is missing
// because the upload never completed). Without this the model is silent about them,
// or worse, answers as if it had seen them.
export const unavailableImagesInstruction = (unavailableFileNames) => {
    const names = unavailableFileNames.map((filename) => `- ${filename}`).join('\n');
    return `Image Attachment Error:\nThe user attached the following image(s), but they could not be loaded and you cannot see them:\n${names}\n\nDo not guess at or describe the contents of these images. Tell the user that the image(s) could not be loaded and ask them to attach them again.`;
}

/**
 * Combine the reference labels for the images we did load with an error notice for
 * the ones we could not. Returns '' when there is nothing to say.
 */
export const buildImageInstruction = (loadedFileNames = [], unavailableFileNames = []) => {
    const parts = [];
    if (loadedFileNames.length > 0) parts.push(additionalImageInstruction(loadedFileNames));
    if (unavailableFileNames.length > 0) parts.push(unavailableImagesInstruction(unavailableFileNames));
    return parts.join('\n\n');
}
