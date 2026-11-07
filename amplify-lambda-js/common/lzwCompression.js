//Copyright (c) 2024 Vanderbilt University
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

/**
 * LZW Compression/Decompression utilities
 * Used for artifact content compression
 */

export function lzwCompress(strInput) {
    if (!strInput) return [];
    const dictionary = new Map();
    let nextCode = 256;
    let compressedOutput = [];

    for (let i = 0; i < 256; i++) {
        dictionary.set(String.fromCharCode(i), i);
    }

    // Preprocessing to convert Unicode characters to a unique format.
    // Wire format (shared with pycommon/lzw.py and the frontend utils/app/lzwCompression.ts; keep all three in sync):
    //  * a UTF-16 code unit > 255 becomes `U+` + exactly 4 lowercase hex digits (zero padded)
    //  * a literal "U+" in the text is written as the tag for 'U' (`U+0055`) followed by '+', so text such as
    //    "U+0041" can never be mistaken for a tag
    let processedInput = '';
    for (let i = 0; i < strInput.length; i++) {
        const code = strInput.charCodeAt(i);
        if (code > 255) {
            processedInput += `U+${code.toString(16).padStart(4, '0')}`;
        } else if (code === 0x55 && strInput.charCodeAt(i + 1) === 0x2b) {
            processedInput += 'U+0055';
        } else {
            processedInput += strInput[i];
        }
    }

    let currentPattern = '';
    for (let character of processedInput) {
        const newPattern = currentPattern + character;
        if (dictionary.has(newPattern)) {
            currentPattern = newPattern;
        } else {
            compressedOutput.push(dictionary.get(currentPattern));
            dictionary.set(newPattern, nextCode++);
            currentPattern = character;
        }
    }

    if (currentPattern !== '') {
        compressedOutput.push(dictionary.get(currentPattern));
    }

    return compressedOutput;
}

export function lzwUncompress(compressedData) {
    if (!compressedData || compressedData.length === 0) return '';

    const dictionary = new Map();
    for (let i = 0; i < 256; i++) {
        dictionary.set(i, String.fromCharCode(i));
    }

    let decompressedString = '';
    let previousEntry = dictionary.get(compressedData[0]);
    if (!previousEntry) {
        return '';
    }
    decompressedString += previousEntry;

    let nextCode = 256;
    for (let i = 1; i < compressedData.length; i++) {
        const currentCode = compressedData[i];
        let currentEntry;
        if (dictionary.has(currentCode)) {
            currentEntry = dictionary.get(currentCode);
        } else if (currentCode === nextCode) {
            currentEntry = previousEntry + previousEntry.charAt(0);
        } else {
            throw new Error('Invalid compressed data: Entry for code not found');
        }

        decompressedString += currentEntry;
        dictionary.set(nextCode++, previousEntry + currentEntry.charAt(0));
        previousEntry = currentEntry;
    }

    // Postprocessing to convert the tagged Unicode characters back to their original form.
    // The second alternative reads data written by the old (unpadded, 3 digit) encoder; it only applies when no
    // further hex digit follows, otherwise the tag is ambiguous and the 4 digit reading wins.
    // The "U+" prefix is case sensitive, so user text such as "u+0041" stays as typed.
    const unicodePattern = /U\+([0-9a-fA-F]{4})|U\+([0-9a-fA-F]{3})(?![0-9a-fA-F])/g;
    const output = decompressedString.replace(unicodePattern, (_, hex4, hex3) => {
        return String.fromCharCode(parseInt(hex4 ?? hex3, 16));
    });

    return output;
}

export function isLzwCompressed(data) {
    return Array.isArray(data) && data.length > 0 && data.every(item => typeof item === 'number');
}
