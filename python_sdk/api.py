"""The API client will handle communication with the Anam API for generating session tokens and fetching engine details"""
import requests

class AnamAPI:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = 'https://api.anam.ai'  # Example, replace with the correct one

    def get_session_token(self, persona_id):
        url = f'{self.base_url}/sessions'
        headers = {'Authorization': f'Bearer {self.api_key}'}
        response = requests.post(url, json={'persona_id': persona_id}, headers=headers)
        response.raise_for_status()
        return response.json()['session_token']

    def get_engine_details(self, session_token):
        url = f'{self.base_url}/engines'
        headers = {'Authorization': f'Bearer {session_token}'}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()