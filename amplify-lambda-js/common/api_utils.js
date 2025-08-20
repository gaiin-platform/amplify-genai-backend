import crypto from 'crypto';

/**
 * Base Token class
 */
class Token {
    constructor(key, salt) {
        this.key = key;
        this.salt = salt;
    }
}

/**
 * Represents a version 1 token with a key and salt.
 * 
 * Inherits from the Token class.
 */
class TokenV1 extends Token {
    /**
     * We do not salt this token due to the high entropy of the key and
     * the fact that it is already a secure random value and, finally, because
     * the hash is used as an index in the database.
     * Remember salting is used to prevent rainbow table attacks, but in this case,
     * the key is already a secure random value with high entropy.
     */
    constructor(key = "") {
        if (typeof key !== 'string') {
            throw new TypeError("Key must be a string.");
        }

        const salt = "";
        const identifier = "amp-";
        let rawKey;
        let hashedKey;

        if (key !== "") {
            if (!key.startsWith(identifier)) {
                throw new Error(`TokenV1 key must start with '${identifier}'.`);
            }
            rawKey = key;
            hashedKey = TokenV1._keyGenerator(rawKey, salt);
        } else {
            // Generate a secure random token similar to Python's secrets.token_urlsafe(32)
            const randomBytes = crypto.randomBytes(32);
            const urlSafeToken = randomBytes.toString('base64url');
            rawKey = `${identifier}v1-${urlSafeToken}`;
            hashedKey = TokenV1._keyGenerator(rawKey, salt);
        }

        super(hashedKey, salt);
        this._salt = salt;
        this._rawKey = rawKey;
        this._key = hashedKey;
        this._identifier = identifier;
    }

    /**
     * Generates a hashed key using the raw key and salt.
     * 
     * Note that in the V1 token, the salt is not used in the hashing process
     * because the key is already a secure random value with high entropy.
     * 
     * @param {string} rawKey - The raw key to be hashed.
     * @param {string} salt - The salt to be used in the hashing process.
     * @returns {string} The hashed key as a hex string.
     */
    static _keyGenerator(rawKey, salt = "") {
        const hash = crypto.createHash('shake256', { outputLength: 64 });
        hash.update(rawKey + salt);
        return hash.digest('hex');
    }

    /**
     * Validates a raw key against the token's key.
     * 
     * @param {string} rawKey - The raw key to validate.
     * @returns {boolean} True if the raw key is valid, false otherwise.
     */
    validate(rawKey) {
        const hashedInput = TokenV1._keyGenerator(rawKey, this._salt);
        // Using crypto.timingSafeEqual for constant-time comparison (similar to secrets.compare_digest)
        const keyBuffer = Buffer.from(this.key, 'hex');
        const inputBuffer = Buffer.from(hashedInput, 'hex');
        
        if (keyBuffer.length !== inputBuffer.length) {
            return false;
        }
        
        return crypto.timingSafeEqual(keyBuffer, inputBuffer);
    }

    /**
     * Compares the provided value with the token's key.
     * 
     * @param {string} value - The value to compare with the token's key.
     * @returns {boolean} True if the value matches the token's key, False otherwise.
     * 
     * @example
     * const token = new TokenV1();
     * const userKey = token.rawKey; // This is the key to be provided to the user
     * const privateKey = token.key; // This is the hashed key to be stored in the database
     * token.equals(userKey); // This will return true if the userKey matches the token's key
     */
    equals(value) {
        if (typeof value !== 'string') {
            throw new TypeError("TokenV1 can only be compared with a string value.");
        }
        return this.validate(value);
    }

    /**
     * This property retrieves the raw key of the token.
     * The raw key is the original key generated during instantiation.
     * This is the key that should be provided to the user for API access.
     * It is not hashed and should be kept secret and will not
     * be stored in the database.
     * 
     * @returns {string} The raw key of the token.
     */
    get rawKey() {
        return this._rawKey;
    }
}

export {
    Token,
    TokenV1
};
