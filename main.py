
# Import libraries for speech recognition, text-to-speech, and OpenAI interaction
import speech_recognition     # For capturing and recognizing spoken input
import pyttsx3                # For converting text responses to speech
from openai import OpenAI     # OpenAI SDK for accessing language models


# Initialize the OpenAI API client with your API key
client = OpenAI(
  api_key="PUT_YOUR_API_KEY_HERE"  # TO DO: Replace with your actual OpenAI API key
)


# Create a recognizer instance for capturing audio input
robot_ear = speech_recognition.Recognizer()

# Initialize the text-to-speech engine
robot_mouth = pyttsx3.init()

# Initial greeting message from the AI agent
robot_brain = "Hi, I'm your AI assistant and my name is VIRA. How can I help you today?"

# Print and speak the greeting
print("AI Agent: " + robot_brain)
robot_mouth.say(robot_brain)
robot_mouth.runAndWait()

# Main loop: Keep listening and responding until user says "bye"
while True:
    with speech_recognition.Microphone() as mic:
        print("AI Agent: Listening...")

        # Listen for user's voice input
        audio = robot_ear.listen(mic)

    # Try to convert speech to text using Google Speech Recognition
    try:
        you = robot_ear.recognize_google(audio)
    except:  
        you = ""  # If recognition fails, default to empty string

    print("You: " + you)

    # Generate a response using OpenAI GPT model
    try:
        completion = client.chat.completions.create(
        model="gpt-4.1-nano",    # You may need to change to a valid model like "gpt-3.5-turbo"
        messages=[
            {"role": "system", "content": "You are a helpful assistant that provides concise and accurate answers to user queries."},
            {"role": "user", "content": you},
        ],
        temperature=0.7,   # Controls creativity of response
        max_tokens=100     # Limits length of the response
        )

        # Extract the response text from the completion result
        robot_brain = completion.choices[0].message.content

        # If user says "bye", exit the loop after responding
        if "bye" in you:
            print("AI Agent: " + robot_brain)
            robot_mouth.say(robot_brain)
            robot_mouth.runAndWait()
            break

  
    # Handle errors during API call
    except:
        robot_brain = "I'm sorry, I couldn't process your request at the moment. Please try again later."
        print("AI Agent: " + robot_brain)

    # Speak and print the AI's response
    print("AI Agent: " + robot_brain)
    robot_mouth.say(robot_brain)
    robot_mouth.runAndWait()
