import os
from pymongo import MongoClient
from dotenv import load_dotenv
import datetime

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://stackoverflow:stackoverflow%40123@cluster0.3kqbc.mongodb.net/myDatabase?retryWrites=true&w=majority&appName=Cluster0")
DB_NAME = os.getenv("MONGO_DB_NAME", "profile_db")
COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME", "candidate_profiles")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]



def insert_meeting(members, agenda, timing, meeting_url):
    """Insert a meeting into the MongoDB 'meetings' collection."""
    meeting_doc = {
        "members": members,  # list of emails or names
        "agenda": agenda,
        "timing": timing,  # should be a datetime object or ISO string
        "meeting_url": meeting_url,
        "created_at": datetime.datetime.utcnow()
    }
    meetings_collection = db["meetings"]
    result = meetings_collection.insert_one(meeting_doc)
    return str(result.inserted_id)


def insert_into_results(user, id, result):
    """Insert a result into the MongoDB collection."""
    result_dict = {
        "user": user,
        "id": id,
        "result": result,
        "timestamp": datetime.datetime.utcnow()
    }
    results_collection = db["results"]
    result = results_collection.insert_one(result_dict)
    return str(result.inserted_id)

def query_mongo_db_for_results(user, id):
    """Query the results collection for a specific user and ID, sorted by timestamp and return the first match."""
    results_collection = db["results"]
    result = results_collection.find_one({"user": user, "id": id}, {"_id": 0, "result": 1})
    if not result:
        return {"error": "Results not found"}
    return result["result"]


def insert_candidate_profile(profile_dict):
    """Insert a candidate profile into the MongoDB collection."""
    result = collection.insert_one(profile_dict)
    return str(result.inserted_id)

def query_mongo_db_for_candidate_profile():
    """Query the candidate profile collection for Samarth Mahendra and return the first match as a JSON-serializable dict."""
    profile = collection.find_one({"name": "Samarth Mahendra"})
    if not profile:
        return {"error": "Profile not found"}
    profile.pop('_id', None)
    def serialize_value(val):
        if isinstance(val, list):
            # If it's a list of dicts, keep as is; if list of primitives, join as string
            if all(isinstance(item, dict) for item in val):
                return val
            return ', '.join(str(item) for item in val)
        if isinstance(val, dict):
            return {k: serialize_value(v) for k, v in val.items()}
        return val

    profile = {k: serialize_value(v) for k, v in profile.items()}
    return profile

def query_phone_numbers(name):
    print(" Querying phone numbers for:", name)
    collections = db["phone_numbers"]
    phone_numbers = collections.find_one({"name": name})
    if not phone_numbers:
        return {"error": "Phone numbers not found"}
    phone_numbers.pop('_id', None)
    # serialize the phone numbers
    def serialize_value(val):
        if isinstance(val, list):
            # If it's a list of dicts, keep as is; if list of primitives, join as string
            if all(isinstance(item, dict) for item in val):
                return val
            return ', '.join(str(item) for item in val)
        if isinstance(val, dict):
            return {k: serialize_value(v) for k, v in val.items()}
        return val
    phone_numbers = {k: serialize_value(v) for k, v in phone_numbers.items()}
    return phone_numbers

    # Ensure all values are JSON-serializable and readable
def save_tool_message(call_id, name, args, result):
    """Save a tool message to the messages collection."""
    message_dict = {
        "message_id": call_id,
        "tool_name": name,
        "args": args,
        "content": result,
        "timestamp": datetime.datetime.utcnow(),
        "status": "completed" if result else "pending"
    }
    messages_collection = db["messages"]
    insert_result = messages_collection.insert_one(message_dict)
    return str(insert_result.inserted_id)


def get_tool_message_status(message_id):
    """Get the status of a tool message by its ID."""
    print(" [MongoDB] Getting message status for ID:", message_id)


    messages_collection = db["messages"]
    message = messages_collection.find_one({"message_id": message_id})

    if not message:
        return "not_found", {}
    print(message)
    print(" [MongoDB] Message status:", message["status"])
    # Remove MongoDB's _id field which is not JSON serializable
    message.pop('_id', None)
    
    # Return the complete message data
    print(" [MongoDB] Message data:", message["content"])
    return message["status"], message["content"]



if __name__ == "__main__":
    # Insert the complete profile for Samarth Mahendra


    profile_dict = {
    "name": "Samarth Mahendra",
    "headline": "Backend Engineer, LLM & Data Systems Enthusiast",
    "linkedin": "https://www.linkedin.com/in/samarth-mahendra-7aab5a114/",
    "github": "https://github.com/SamarthMahendra",
    "portfolio": "https://samarthmahendra.github.io/",
    "youtube": "https://www.youtube.com/@msamarthmahendra8082",
    "bio": (
        "Currently building a distributed job tracking system (JobStats) and a personal profile website with an agent for interview scheduling. "
        "Looking to collaborate on LLM-powered productivity tools, backend infra, or real-time systems. Learning advanced DBs, mobile dev, and distributed design. "
        "Ask me about chatbot optimization, API cost reduction, and fun fact: skated 22.3 km in one go!"
    ),
    "skills": sorted(list(set([
        "Python", "Java", "C", "C++", "JavaScript", "TypeScript", "MongoDB", "LLM", "Celery", "Redis", "Prometheus", "Puppeteer", "React",
        "PostgreSQL", "Django", "Flask", "Linux", "Unix", "Kubernetes", "Terraform", "AWS", "Azure", "Firebase", "MySQL", "PyTorch", "NumPy",
        "CuPy", "Multiprocessing", "Jenkins", "Jira", "Git", "GitLab", "Bitbucket", "Android", "CSS3", "DigitalOcean", "Jest", "SQL", "Pytest",
        "Docker", "ChromaDB", "Elasticsearch", "Playwright", "LLM Integration", "System Design", "Data Modeling", "REST APIs", "Microservices", "Problem Solving", "twilio", "GPT-4o realtime", "agents", "LLM orchestraction","websockets"
    ]))),
    "education": [
        {
            "institution": "Northeastern University, Boston, MA",
            "degree": "Master of Science (MS), Computer Science",
            "dates": "Jan 2024 – Dec 2025",
            "courses": [
                "CS 5010: Programming Design Paradigm",
                "CS 5200: Database Management Systems",
                "CS 5800: Algorithms",
                "CS 6120: Natural Language Processing",
                "CS 6140: Machine Learning",
                "CS 5520: Mobile Application Development",
                "CS 5500: Foundations of Software Engineering"
            ]
        },
        {
            "institution": "Dayananda Sagar College of Engineering, Bangalore, India",
            "degree": "Bachelor of Engineering (BE), Computer Science",
            "dates": "Aug 2018 – Jul 2022",
            "cgpa": 8.59,
            "courses": """
             C Programming for Problem Solving
Discrete Mathematical Structures
Data Structures with Applications
Object Oriented Programming with Java
Internet & Web Programming
Computer Organization
Logic Design
Data Structures Laboratory with Applications
Foundation in Mathematics for Computing
Design and Analysis of Algorithms
Microprocessors & Microcontrollers
Operating System
Automata Theory and Formal Languages
Database Management System
Computer Networks
Artificial Intelligence and Machine Learning
Software Engineering
Cryptography
Object Oriented Modeling and Design
Engineering Economics
System Software
Cyber Security
Cloud Computing Applications
Internet of Things
Big Data Analytics
Wireless Sensor Network
Blockchain Technologies
Cloud & Big Data Laboratory with ProjectC Programming for Problem Solving Discrete Mathematical Structures Data Structures with Applications Object Oriented Programming with Java Internet & Web Programming Computer Organization Logic Design Data Structures Laboratory with Applications Foundation in Mathematics for Computing Design and Analysis of Algorithms Microprocessors & Microcontrollers Operating System Automata Theory and Formal Languages Database Management System Computer Networks Artificial Intelligence and Machine Learning Software Engineering Cryptography Object Oriented Modeling and Design Engineering Economics System Software Cyber Security Cloud Computing Applications Internet of Things Big Data Analytics Wireless Sensor Network Blockchain Technologies Cloud & Big Data Laboratory with Project
"""
        }
    ],
    "experience": [
        {
            "role": "Associate Software Development Engineer – Backend",
            "company": "Draup (Startup)",
            "type": "Full-time",
            "dates": "Aug 2022 – Nov 2023",
            "location": "Bangalore (Hybrid)",
            "highlights": [
                "Led platform modules for digital tech stack, outsourcing, and customer intelligence.",
                "Designed and developed dynamic query generation engine (60% performance gain, 80% dev time cut).",
                "Revamped filters with advanced Boolean logic (e.g., (a AND b) OR c).",
                "Authored business logic for 100+ APIs using Python/Django.",
                "Migrated APIs to Elasticsearch (5× speedup); real-time aggregation.",
                "Built subscription-based access control systems.",
                "Optimized platform via indexing, partitioning, views (400% query speedup, 50% cost drop).",
                "Reduced downtime from 4% to 1%; resolved issues 75% faster with Datadog + AWS CloudWatch."
            ]
        },
        {
            "role": "Backend Engineering Intern",
            "company": "Draup (Startup)",
            "type": "Internship",
            "dates": "Apr 2022 – Jul 2022",
            "location": "Bengaluru",
            "highlights": [
                "Debugged API issues using Datadog, improving resolution time by 30%.",
                "Implemented caching for image requests (70% faster load).",
                "Developed automated DB scripts (25% DB efficiency boost)."
            ]
        },
        {
            "role": "Research Assistant (Patent Co-Inventor)",
            "company": "Dayananda Sagar College of Engineering",
            "type": "Part-time",
            "dates": "Nov 2021 – Sep 2023",
            "location": "Bengaluru (Hybrid)",
            "project": "Myocardium Wall Motion & Thickness Mapping (Patent Pending)",
            "app_no": "202341086278 (India)",
            "highlights": [
                "Built cine-MRI processing pipeline for wall motion, thickness & fibrosis detection.",
                "Improved measurement precision in fuzzy zones by 50%.",
                "Reduced execution time by 60× using NumPy + multiprocessing."
            ]
        }
    ],
    "projects": [
        {
            "name": "Open Jobs – Analytics (JobStats)",
            "description": "Job scraping dashboard inspired by Levels.fyi, using Puppeteer, Redis, Celery, LLMs; 500+ jobs/day, stealth headers, Grafana monitoring.",
            "github": "https://github.com/SamarthMahendra/StealthProject"
        },
        {
            "name": "LinkedInAssist (LLM-powered)",
            "description": "Chrome extension to filter LinkedIn jobs via natural language + Boolean modifiers using GPT-3.5 and Flask backend."
        },
        {
            "name": "Live Bluetooth Silent Disco",
            "description": "Real-time WebSocket-based audio streaming system using Python + BlackHole for silent parties."
        },
        {
            "name": "Chatbot for Account Intelligence (Hackathon @ Draup)",
            "description": "Langchain + RAG + Redis + PostgreSQL backend; intelligent account insights with cross-encoder reranking."
        },
        {
            "name": "AI Voice Assistant",
            "description": "GPT-4o + Twilio voice agent with <500ms latency, VAD, streaming API, deployed via FastAPI + WebSocket + Celery; public demo enabled."
        },
        {
            "name": "StackOverflow Clone",
            "description": "Q&A platform using React + Node.js + MongoDB + TypeScript + Cypress + GitHub CI; designed with Strategy, Factory, and Validator patterns."
        },
        {
            "name": "Stock Market Simulator (Java)",
            "description": "MVC-based simulation tool with stock API integration, visualization, and JUnit test suite for investment strategies."
        },
        {
            "name": "Bike Rental Platform",
            "description": "BlueBikes-like full-stack rental app using React, Django REST, Redis, JWT, Netlify, Azure, and Datadog."
        },
        {
            "name": "Myocardium Wall Motion Mapper (Patent)",
            "description": "MRI-based heart wall visualization system; co-inventor on Indian patent (App No: 202341086278)."
        },
        {
            "name": "MapReduce-style Grade Analyzer",
            "description": "Python multiprocessing for analyzing large student datasets."
        },
        {
            "name": "Breast Cancer Detection",
            "description": "Used logistic regression, GNB, and GDA to classify tumor malignancy."
        },
        {
            "name": "Aspect-Based Sentiment Analysis",
            "description": "Attention-based LSTM for sentiment classification in SemEval datasets using PyTorch."
        },
        {
            "name": "Custom Word2Vec",
            "description": "Built co-occurrence matrix from Shakespeare’s Merchant of Venice, visualized using PCA."
        },
        {
            "name": "Unemployment vs Job Openings",
            "description": "Analyzed Beveridge Curve trends using PyTorch and Pandas."
        }
    ],
    "certifications": [
        {"name": "Expert - Programming and Algorithms (CodeSignal)", "credential_id": "cm6lagnfc01ihm8i3wldt2po3"},
        {"name": "Advanced Retrieval for AI with Chroma (DeepLearning.AI)", "credential_id": "e7856493-e9ca-40f3-81a2-62e86fc6267c"},
        {"name": "Supervised ML: Regression & Classification (DeepLearning.AI)", "credential_id": "W7RGEA3RE44U"},
        {"name": "Advanced Learning Algorithms (DeepLearning.AI)", "credential_id": "PC74JUPWD28G"},
        {"name": "DOM API + JS Programming (CodeSignal)", "credential_id": "cm6po6406007ztmrk4bw7za5o"},
        {"name": "Server-Side Web Scraping (CodeSignal)", "credential_id": "cm6n495fv00twy6hg7w0xihzf"},
        {"name": "Mastering Data Structures & Algorithms in Python (CodeSignal)", "credential_id": "cm0adl6mm004lgpxn4gphel9o"}
    ],
    "fun_fact": "I skated 22.3 km in a single session!",
    "Job_preference": {
        "type": "Full-time",
        "location": ["Remote", "Hybrid", "On-site"],
        "preferred_roles": ["Backend Engineer", "Software Engineer", "Senior Software Engineer", "SDE", "SDE-1", "SDE-2"]
    },
    "contact": {
        "email": "samarth.mahendragowda@gmail.com",
        "phone": "+1 (857) 707-1671",
        "location": "Boston, MA, USA"
    },
    "date_updated": str(datetime.datetime.utcnow()),
    "Phone": "+1 (857) 707-1671",
    "Location": "Boston, MA, USA",
    "Availability": "Immediate",
    "email" : "samarth.mahendragowda@gmail.com",
}
    print("Inserted profile ID:", insert_candidate_profile(profile_dict))


    # Example: query
    print(query_mongo_db_for_candidate_profile())
