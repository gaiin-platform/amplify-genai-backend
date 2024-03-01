// Set the environment variables before requiring your modules
process.env.COGNITO_USER_POOL_ID = "us-east-1_eNmQM0hYG";
process.env.COGNITO_CLIENT_ID = "736iao9oeeosvreonef5e9ej0m";

// Now require the handler module, which will use the variables you've just set
const { handler } = require('./index');

// Mock event object
const event = {
    // Add the necessary structure that your Lambda expects
    // For example, if your Lambda expects an API Gateway event:
    headers: {
        Authorization: "Bearer eyJraWQiOiJxUUNXZ2RJK25rWSs4SWpcL1BieUNzTDZYUkNFXC8yXC94bDBhWGx4Tmx6c1RzPSIsImFsZyI6IlJTMjU2In0.eyJzdWIiOiJhOTg5ZWQ1MS04NTNiLTQwYzEtYmZkNy1kNzE1MDAxY2UzOTIiLCJjb2duaXRvOmdyb3VwcyI6WyJ1cy1lYXN0LTFfZU5tUU0waFlHX1ZVUGluZ0lkcCJdLCJpc3MiOiJodHRwczpcL1wvY29nbml0by1pZHAudXMtZWFzdC0xLmFtYXpvbmF3cy5jb21cL3VzLWVhc3QtMV9lTm1RTTBoWUciLCJ2ZXJzaW9uIjoyLCJjbGllbnRfaWQiOiI3MzZpYW85b2Vlb3N2cmVvbmVmNWU5ZWowbSIsIm9yaWdpbl9qdGkiOiIxN2RmZmEzZS01NTYyLTQzZTctYTU0NS02M2NjNTRjZWJlMDQiLCJ0b2tlbl91c2UiOiJhY2Nlc3MiLCJzY29wZSI6Im9wZW5pZCIsImF1dGhfdGltZSI6MTcwOTMwNTMwNywiZXhwIjoxNzA5MzkxNzA3LCJpYXQiOjE3MDkzMDUzMDcsImp0aSI6ImM0ZWIxNTJmLTVmMjUtNDY3Yi1iMzg4LTkyOWMzNjkwNWRjZCIsInVzZXJuYW1lIjoidnVwaW5naWRwX21heGltaWxsaWFuLnIubW91bmRhc0B2YW5kZXJiaWx0LmVkdSJ9.b0pgfq_MxoOj7SNmZie_DBnYvePz8CQZrTQErbLivV6vbFM33RTi9h9SFCYgeuEjPdj8OVc51JpzaZhmI7T0TVKKi-VGeclTWCcJ5nAH1456BxKue7olfk1MwwqJCKVBDJqfBIsfbZpSARqKKTU4EDOALiA2cMMnusU6HUNSVnHxHI3hshtpvuehYR07Da0OhOAlCm45BTh5ukP0OlhKw8dzyh_41e-OpNUN1sxiuleeT0pj4G_-KEhu1sO6raOaj0xzbT9Fo6jRHJJGpHtuexti4Hyv1aKVirf4Hn17vlxN8IbpuYCnOGTzPvJuoENiOArsrfviyjdL00u6CtSC_A"
    },
    body: JSON.stringify({
        action: "scrape_url",
        url: "https://en.wikipedia.org/wiki/ChatGPT" // The URL you want to scrape
    })
};

// Callback function to handle the response
const callback = (error, response) => {
    if (error) {
        console.error("Error:", error);
    } else {
        const responseBody = JSON.parse(response.body);
        console.log("Response Body:", responseBody);
    }
};

// Invoke the handler
handler(event, {}, callback);
