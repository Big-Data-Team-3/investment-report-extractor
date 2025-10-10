from guidance import system, user, assistant, gen
from guidance.models import OpenAI
import os 
from dotenv import load_dotenv

def load_env():
    load_dotenv()
    os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY")
    return os.getenv("OPENAI_API_KEY")

def load_model():
    lm = OpenAI("gpt-3.5-turbo",api_key=load_env())
    return lm

def guidance_pipeline(system_prompt: str, prompt: str):
    lm = load_model()
    with system():
        lm += system_prompt
    with user():
        lm += prompt
    with assistant():
        lm += gen(name="response")
    return lm["response"]


if __name__ == "__main__":
    system_prompt = "You are a helpful assistant. Your name now is Hessian. Your role is to provide the user with structured responses whenever necessary."
    prompt = "Give the list of DOW30 companies."
    print(guidance_pipeline(system_prompt, prompt))