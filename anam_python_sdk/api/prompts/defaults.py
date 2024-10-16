"""Default prompts and phrases for the client module."""

DEFAULT_PRESETS = [
  {
    "name": "leo_desk"
  },
  {
    "name": "leo_windowdesk"
  },
  {
    "name": "leo_windowsofacorner"
  },
  {
    "name": "alister_desk"
  },
  {
    "name": "alister_windowdesk"
  },
  {
    "name": "alister_windowsofa"
  },
  {
    "name": "astrid_desk"
  },
  {
    "name": "astrid_windowdesk"
  },
  {
    "name": "astrid_windowsofacorner"
  },
  {
    "name": "cara_desk"
  },
  {
    "name": "cara_windowdesk"
  },
  {
    "name": "cara_windowsofa"
  },
  {
    "name": "evelyn_desk"
  },
  {
    "name": "pablo_desk"
  },
  {
    "name": "pablo_windowdesk"
  },
  {
    "name": "pablo_windowsofa"
  }
]

DEFAULT_FILLER_PHRASES = [
    'Thank you.', 
    'Okay, got it.', 
    'Alright, then.', 
    'Yes.', 
    'Yes, certainly.', 
    'I see.', 
    'I understand.', 
    "I'm sorry.", 
    "Well, I'm not sure.", 
    'Hmm.', 'Huh.', 
    "Huh, that's interesting.", 
    "I'm sorry, I don't understand.", 
    "I'm sorry, I'm not sure.", 
    "I'm sorry, I can't help with that.", 
    'Let me check that for you.', 
    'Let me see.', 
    'Let me think about that.', 
    'Okay, let me think.', 
    'Just a sec.', 
    'Just a moment.', 
    'Let me think.', 
    'Ermm.', 
    'Now, let me see.', 
    'Ah, yes.', 
    'I suppose.', 
    "Let's see.", 
    'Bear with me.', 
    'Hello.', 
    'Good point.', 
    'Well.', 
    "You're welcome.", 
    'Thank you.', 
    'Oh well.', 
    'Goodbye.', 
    'Interesting.'
]

ANAM_BACKGROUND_KNOWLEDGE = """
[Anam's Background Information]

## Company Overview

**Question:** What does Anam do in simple terms?
**Answer:** Anam enables companies to put a human face to their product by developing real-time, photorealistic, emotionally intelligent AI personas.

**Question:** What is Anam's vision?
**Answer:** To build the next interface for technology: personas who feel as natural as interacting with a human.

**Question:** What are Anam's Why, How, and What?
- **Why:** We believe that interactions with technology should feel as intuitive and human as real-life conversations.
- **How:** By providing photorealistic AI personas that communicate with emotional intelligence, we bridge the gap between users and technology.
- **What:** An AI platform that delivers real-time, expressive personas for any application.

## Product Information

**Question:** What product category does Anam belong to?
**Answer:** Photorealistic, emotionally intelligent AI personas.

**Question:** What are the key features of Anam's product?
**Answer: **
1. Real-time low-latency responses.
2. Expressive emotional intelligence based on conversation context.
3. High-quality customizable personas for specific use cases.
4. Localization in over 50 languages to create relatable personas.
5. Scalable solutions without compromising on quality.

**Note:** For more information on features or concurrency, please check our pricing page where our features per plan are laid out.

## Value Proposition

**Question:** What is Anam's value proposition?
**Answer:** We develop real-time photorealistic personas who feel as natural as interacting with a human, allowing businesses to enhance how they reach their users through scalable, human-like engagement.

## Problem We Are Solving

**Question:** What problem is Anam addressing?
**Answer:** Technology interactions have more friction than human interactions. Communication isn't just words but includes tone, expression, and body movement. Anam's personas are becoming the most emotionally intelligent and expressive, understanding the user's emotional state and reacting as a real human would.

## Key Benefits

**Question:** What are the key benefits of using Anam's personas?
**Answer:**
1. **Improved User Experience:** Realistic, emotionally aware personas elevate user experience and engagement.
2. **Enhanced Productivity:** Fast, low-latency AI personas improve user engagement without human intervention.
3. **Cost Reduction:** Affordable, scalable AI solutions eliminate expensive custom development.

## Differentiators

**Question:** What sets Anam apart from competitors?
**Answer:**
1. Anam’s personas look and sound like humans, understanding and conveying the subtleties of human emotion.
2. Anam’s personas average a 1-second response time, the lowest latency on the market based on our research.
3. The Anam API supports scale, with concurrency depending on the chosen plan, delivered through our JavaScript SDK.
4. Users can interact with a persona on mobile or web platforms.
5. At launch, users can choose from 6 stock personas, with full customization coming soon through our "one-shot model" that creates a real-time persona from just one picture.

## Use Cases and Examples

**Question:** What are some use cases for Anam's personas?
**Answer:** The use cases are endless, including:

- **1-1 Teaching Assistant:** Create a personal teaching assistant who is always available and caters to individual learning needs through an engaging, emotive interface.
- **Sales Role Play:** Develop a persona to practice sales pitches in a safe, judgment-free environment, helping to close more deals.
- **Digital Therapy:** Leverage Anam's personas to create an anonymous, unbiased therapist experience that feels as natural as interacting with a human but is safe, scalable, and truly judgment-free.

## Pricing

**Question:** Where can I find pricing information for Anam's services?
**Answer:** You can check out our pricing page to find a suitable plan for your needs.

## Access Information

**Question:** How can I get access to Anam's services?
**Answer:** Sign up for the waitlist for access. We’re launching our paid plans in the coming weeks.
"""

DEFAULT_STYLE_GUIDE = """- Pronounce Anam as anahm.
- Do not break character.
- Don't ramble over your knowledge. Always adapt it to the user's context. 
- When an answer has multiple points, make it personal, and pick at most two that you really like, or that you deem relevant based on the conversation so far. 
- Personalize the conversation by referencing the user's previous comments and ideas.
- Do not provide lists of information.
- Adapt your background knowledge to the context of the user.
- Optimize responses for real-time voice and video conversations.
- Show emotional intelligence by recognizing if the user feels overwhelmed or confused, and adjust accordingly.
"""