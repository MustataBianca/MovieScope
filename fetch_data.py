import os
import requests
import pandas as pd
import time
import numpy as np
import urllib.parse
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()
api_token = os.getenv("API_TOKEN")

headers = {
    "accept": "application/json",
    "Authorization": f"Bearer {api_token}"
}

print("1. Fetching official Genres mapping...")
url_genres = "https://api.themoviedb.org/3/genre/movie/list"
response_genres = requests.get(url_genres, headers=headers, params={"language": "en-US"})
df_genres = pd.DataFrame(response_genres.json()["genres"])


print("2. Fetching top movies list...")
url_movies = "https://api.themoviedb.org/3/movie/popular"
basic_movies_list = []

for page in range(1, 26): # 25 pages * 20 movies per page = 500 movies
    params = {"language": "en-US", "page": page}
    response = requests.get(url_movies, headers=headers, params=params)
    
    if response.ok:
        basic_movies_list.extend(response.json()["results"])
    else:
        print(f"Error fetching page {page}.")
        break
        
    time.sleep(0.1) # delay to avoid the API rate limit

print("3. Fetching Collections, Reviews, and Users...")
collections_dict = {}
users_dict = {}
reviews_list = []
movie_genres_mapping = []
final_movies_list = []

user_id_counter = 1
review_id_counter = 1

for movie in basic_movies_list:
    movie_id = movie['id']
    
    # N:N relationship between Movies and Genres
    for g_id in movie.get('genre_ids', []):
        movie_genres_mapping.append({'movie_id': movie_id, 'genre_id': g_id})
    
    details_url = f"https://api.themoviedb.org/3/movie/{movie_id}"
    resp_details = requests.get(details_url, headers=headers)
    
    collection_id = None
    if resp_details.ok:
        details = resp_details.json()
        collection = details.get("belongs_to_collection")
        if collection:
            collection_id = collection["id"]
            # Store unique collections
            if collection_id not in collections_dict:
                collections_dict[collection_id] = {
                    "id": collection_id,
                    "name": collection.get("name"),
                    "poster_path": collection.get("poster_path")
                }
    time.sleep(0.1) # Rate limit
    
    reviews_url = f"https://api.themoviedb.org/3/movie/{movie_id}/reviews"
    resp_reviews = requests.get(reviews_url, headers=headers)
    
    if resp_reviews.ok:
        reviews_data = resp_reviews.json().get("results", [])
        for rev in reviews_data:
            username = rev["author_details"]["username"]
            
            # Create user if it doesn't exist
            if username not in users_dict:
                users_dict[username] = {
                    "id": user_id_counter,
                    "username": username,
                    "email": None,
                    "password_hash": None,
                    "is_tmdb_import": True # Identifies pre-populated users from TMDb
                }
                user_id_counter += 1
            
            reviews_list.append({
                "id": review_id_counter,
                "movie_id": movie_id,
                "user_id": users_dict[username]["id"],
                "content": rev["content"],
                "created_at": rev["created_at"]
            })
            review_id_counter += 1
    time.sleep(0.1) # Rate limit

    rel_date = movie.get('release_date')
    if not rel_date:
        rel_date = None

    final_movies_list.append({
        'id': movie['id'],
        'title': movie['title'],
        'release_date': rel_date,
        'overview': movie.get('overview'),
        'vote_average': movie.get('vote_average'),
        'vote_count': movie.get('vote_count'),
        'collection_id': collection_id
    })

df_movies = pd.DataFrame(final_movies_list).drop_duplicates(subset=['id'])
df_collections = pd.DataFrame(list(collections_dict.values()))
df_users = pd.DataFrame(list(users_dict.values()))
df_reviews = pd.DataFrame(reviews_list).drop_duplicates(subset=['id'])
df_movie_genres = pd.DataFrame(movie_genres_mapping).drop_duplicates(subset=['movie_id', 'genre_id'])

# Ensure datetime columns are correctly formatted for SQL Server
if not df_reviews.empty:
    df_reviews['created_at'] = pd.to_datetime(df_reviews['created_at'], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')
df_movies['release_date'] = pd.to_datetime(df_movies['release_date'], errors='coerce').dt.strftime('%Y-%m-%d')

# Transformăm toate valorile lipsă (NaN)  în None pentru a fi trimise ca NULL în SQL
df_movies = df_movies.replace({np.nan: None, 'NaT': None})
df_collections = df_collections.replace({np.nan: None, 'NaT': None})
df_users = df_users.replace({np.nan: None, 'NaT': None})
if not df_reviews.empty:
    df_reviews = df_reviews.replace({np.nan: None, 'NaT': None})
if not df_movie_genres.empty:
    df_movie_genres = df_movie_genres.replace({np.nan: None, 'NaT': None})

print("\n5. Creating strict SQL Schema with Constraints in Azure...")
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
engine = create_engine(f"mssql+pyodbc:///?odbc_connect={params_db}", fast_executemany=True)

schema_sql = """
-- Drop existing tables to rebuild the schema cleanly
DROP TABLE IF EXISTS MovieGenres;
DROP TABLE IF EXISTS Reviews;
DROP TABLE IF EXISTS Movies;
DROP TABLE IF EXISTS Collections;
DROP TABLE IF EXISTS Genres;
DROP TABLE IF EXISTS Users;

-- 1. Genres Table
CREATE TABLE Genres (
    id INT PRIMARY KEY,
    name NVARCHAR(100) NOT NULL
);

-- 2. Collections Table
CREATE TABLE Collections (
    id INT PRIMARY KEY,
    name NVARCHAR(255) NOT NULL,
    poster_path NVARCHAR(255)
);

-- 3. Users Table
CREATE TABLE Users (
    id INT PRIMARY KEY,
    username NVARCHAR(255) NOT NULL,
    email NVARCHAR(255),
    password_hash NVARCHAR(255),
    is_tmdb_import BIT DEFAULT 1
);

-- 4. Movies Table
CREATE TABLE Movies (
    id INT PRIMARY KEY,
    title NVARCHAR(255) NOT NULL,
    release_date DATE,
    overview NVARCHAR(MAX),
    vote_average FLOAT CHECK (vote_average >= 0 AND vote_average <= 10),
    vote_count INT CHECK (vote_count >= 0),
    collection_id INT,
    FOREIGN KEY (collection_id) REFERENCES Collections(id)
);

-- 5. Reviews Table
CREATE TABLE Reviews (
    id INT PRIMARY KEY,
    movie_id INT NOT NULL,
    user_id INT NOT NULL,
    content NVARCHAR(MAX) NOT NULL,
    created_at DATETIME,
    FOREIGN KEY (movie_id) REFERENCES Movies(id),
    FOREIGN KEY (user_id) REFERENCES Users(id)
);

-- 6. MovieGenres Junction Table
CREATE TABLE MovieGenres (
    movie_id INT,
    genre_id INT,
    PRIMARY KEY (movie_id, genre_id),
    FOREIGN KEY (movie_id) REFERENCES Movies(id),
    FOREIGN KEY (genre_id) REFERENCES Genres(id)
);
"""

try:
    # Execute the DDL script to create the strict schema
    with engine.begin() as conn:
        conn.execute(text(schema_sql))
    print("Strict schema created successfully!")

    # Upload the data using append
    print("Uploading data into the schema...")
    df_genres.to_sql('Genres', con=engine, if_exists='append', index=False)
    df_collections.to_sql('Collections', con=engine, if_exists='append', index=False)
    if not df_users.empty:
        df_users.to_sql('Users', con=engine, if_exists='append', index=False)
    df_movies.to_sql('Movies', con=engine, if_exists='append', index=False)
    if not df_reviews.empty:
        df_reviews.to_sql('Reviews', con=engine, if_exists='append', index=False)
    if not df_movie_genres.empty:
        df_movie_genres.to_sql('MovieGenres', con=engine, if_exists='append', index=False)
    
    print("\nSUCCESS! The database is in Azure.")
except Exception as e:
    print(f"Error during Azure upload: {e}")