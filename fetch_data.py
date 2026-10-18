import os
import requests
import pandas as pd
import time
import urllib.parse
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv()
api_token = os.getenv("API_TOKEN")

headers = {
    "accept": "application/json",
    "Authorization": f"Bearer {api_token}"
}

print("Fetching official Genres mapping...")
url_genres = "https://api.themoviedb.org/3/genre/movie/list"
response_genres = requests.get(url_genres, headers=headers, params={"language": "en-US"})
genres_data = response_genres.json()["genres"]
df_genres = pd.DataFrame(genres_data) # Creates a table with 'id' and 'name'

print("Fetching top 1000 movies...")
url_movies = "https://api.themoviedb.org/3/movie/popular"
all_movies = []

for page in range(1, 51):
    params = {"language": "en-US", "page": page}
    response = requests.get(url_movies, headers=headers, params=params)
    
    if response.ok:
        all_movies.extend(response.json()["results"])
    else:
        print(f"Error on page {page}.")
        break
        
    time.sleep(0.1)

df_movies = pd.DataFrame(all_movies)

print("Building the junction table...")
movie_genres_mapping = []

for index, row in df_movies.iterrows():
    movie_id = row['id']
    genre_ids = row['genre_ids']
    
    for g_id in genre_ids:
        movie_genres_mapping.append({'movie_id': movie_id, 'genre_id': g_id})

df_movie_genres = pd.DataFrame(movie_genres_mapping)

df_clean_movies = df_movies[['id', 'title', 'release_date', 'overview', 'vote_average', 'vote_count']]


print("\nPreparing to send the tables to Azure SQL...")
# Database Connection setup
db_server = os.getenv("DB_SERVER")
db_name = os.getenv("DB_NAME")
db_user = os.getenv("DB_USER")
db_password = os.getenv("DB_PASSWORD")

params_db = urllib.parse.quote_plus(
    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
    f"SERVER={db_server};"
    f"DATABASE={db_name};"
    f"UID={db_user};"
    f"PWD={db_password}"
)
engine = create_engine(f"mssql+pyodbc:///?odbc_connect={params_db}")

#Upload the tables to Azure
try:
    df_genres.to_sql('Genres', con=engine, if_exists='replace', index=False)
    print("Genres table uploaded successfully.")
    
    df_clean_movies.to_sql('Movies', con=engine, if_exists='replace', index=False)
    print("Movies table uploaded successfully.")
    
    df_movie_genres.to_sql('MovieGenres', con=engine, if_exists='replace', index=False)
    print("MovieGenres junction table uploaded successfully.")
    
except Exception as e:
    print(f"Error during database upload: {e}")