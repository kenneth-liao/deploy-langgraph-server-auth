#!/usr/bin/env python3
from mcp.server.fastmcp import FastMCP
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import time
import json
from datetime import datetime
from dotenv import load_dotenv
import os
from rocket.db.server import Video, Comment, db_create_video_record, db_upsert_comments_records
from typing import List
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Add console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


# ----------------------------
# YouTube Comment Extractor
# ----------------------------
    

class YouTubeCommentExtractor:
    """
    Simple YouTube comment extractor for top-level comments only.
    """
    
    def __init__(self):
        """
        Initialize the comment extractor.
        """
        self.api_key = os.getenv("YOUTUBE_DATA_API_KEY")

        if not self.api_key:
            raise ValueError(
                "YOUTUBE_DATA_API_KEY environment variable not found. "
                "Please set it in your .env file or environment."
            )

        try:
            self.youtube = build('youtube', 'v3', developerKey=self.api_key)
        except Exception as e:
            raise ValueError(f"Failed to initialize YouTube API client: {e}")
        
    def extract_video_id_from_url(self, url) -> str:
        """
        Extract video ID from various YouTube URL formats.
        
        Args:
            url (str): YouTube URL
        
        Returns:
            str: Video ID or empty string if not found
        """
        import re
        
        patterns = [
            r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([^&\n?#]+)',
            r'youtube\.com/watch\?.*v=([^&\n?#]+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        return ""
    
    def extract_comments(
            self, 
            video_url: str, 
            max_comments: int = 5,
            order: str = 'time'
            ) -> List[Comment]:
        """
        Extract top-level comments from a YouTube video.

        Args:
            video_id (str): YouTube video ID (e.g., 'dQw4w9WgXcQ')
            max_comments (int, optional): Maximum number of comments to return.
                                        If None, extracts all comments.
            order (str): Order of comments. Options: 'time', 'relevance'

        Returns:
            list: List of comment dictionaries with the following structure:
                {
                    'comment_id': str,
                    'text': str
                    'like_count': int,
                    'reply_count': int
                }
        """
        video_id = self.extract_video_id_from_url(video_url)
        if not video_id:
            return []

        comments = []
        next_page_token = None

        limit_text = f" (limit: {max_comments})" if max_comments else " (all comments)"
        print(f"Extracting top-level comments for video: {video_id}{limit_text}")

        try:
            while True:
                # Calculate how many comments to request in this batch
                if max_comments:
                    remaining = max_comments - len(comments)
                    if remaining <= 0:
                        break
                    batch_size = min(100, remaining)  # API max is 100 per request
                else:
                    batch_size = 100

                # Request comment threads (we only want the top-level comments)
                request = self.youtube.commentThreads().list(
                    part='snippet',  # Only get snippet, not replies
                    videoId=video_id,
                    maxResults=batch_size,
                    order=order,
                    pageToken=next_page_token
                )

                response = request.execute()

                # Process each comment thread
                for item in response['items']:
                    # Check if we've reached the limit
                    if max_comments and len(comments) >= max_comments:
                        break

                    # Extract top-level comment data
                    top_comment = item['snippet']['topLevelComment']['snippet']

                    comment_data = {
                        'id': item['snippet']['topLevelComment']['id'],
                        'text': top_comment['textDisplay'],
                        'like_count': top_comment['likeCount'],
                        'reply_count': item['snippet']['totalReplyCount'],
                        'video_id': video_id
                    }

                    comments.append(Comment.model_validate(comment_data))

                print(f"Extracted {len(comments)} comments so far...")

                # Check if we've reached the limit
                if max_comments and len(comments) >= max_comments:
                    break

                # Check if there are more pages
                next_page_token = response.get('nextPageToken')
                if not next_page_token:
                    break

                # Clear response from memory to prevent accumulation
                del response

                # Be respectful to the API - add a small delay
                time.sleep(0.1)

            # Trim to exact limit if specified
            if max_comments and len(comments) > max_comments:
                comments = comments[:max_comments]

            print(f"Finished! Total top-level comments extracted: {len(comments)}")
            return comments
            
        except HttpError as e:
            print(f"An HTTP error {e.resp.status} occurred: {e.content}")
            
            if e.resp.status == 403:
                error_content = e.content.decode('utf-8')
                if 'commentsDisabled' in error_content:
                    print("Comments are disabled for this video.")
                elif 'quotaExceeded' in error_content:
                    print("API quota exceeded. Try again tomorrow or request a quota increase.")
                else:
                    print("Access forbidden. Check your API key and permissions.")
            elif e.resp.status == 404:
                print("Video not found. Check the video ID.")
            
            return []
        
    def search_videos(self, query: str, max_results: int = 10, **options) -> Dict[str, Any]:
        """
        Search for YouTube videos based on query and options
        """
        try:
            search_params = {
                'part': 'snippet',
                'q': query,
                'maxResults': max_results,
                'type': options.get('type', 'video')
            }
            
            # Add optional parameters if provided
            for param in ['channelId', 'order', 'videoDuration', 'publishedAfter', 
                        'publishedBefore', 'videoCaption', 'videoDefinition', 'regionCode']:
                if param in options and options[param]:
                    search_params[param] = options[param]
            
            response = self.youtube.search().list(**search_params).execute()
            return response
        except HttpError as e:
            logger.error(f"Error searching videos: {e}")
            raise e

    def get_video_info(self, video_url: str) -> Video:
        """
        Get video information including title, description, channel info, etc.

        Args:
            video_id (str): YouTube video ID (e.g., 'dQw4w9WgXcQ')

        Returns:
            dict: Video information with the following structure:
                {
                    'video_id': str,
                    'title': str,
                    'description': str,
                    'channel_title': str,
                    'published_at': str,
                    'duration': str,
                    'view_count': int,
                    'like_count': int,
                    'comment_count': int
                }
        """
        try:
            video_id = self.extract_video_id_from_url(video_url)
            if not video_id:
                raise ValueError("Could not extract video ID from URL")

            # Request video details
            request = self.youtube.videos().list(
                part='snippet,statistics,contentDetails',
                id=video_id
            )

            response = request.execute()

            if not response['items']:
                raise ValueError("Video not found")

            video = response['items'][0]
            snippet = video['snippet']
            statistics = video['statistics']
            content_details = video['contentDetails']

            video_info = {
                'id': video_id,
                'title': snippet.get('title', ''),
                'url': video_url,
                'description': snippet.get('description', ''),
                'channel_title': snippet.get('channelTitle', ''),
                'published_at': snippet.get('publishedAt', ''),
                'duration': content_details.get('duration', ''),
                'view_count': int(statistics.get('viewCount', 0)),
                'like_count': int(statistics.get('likeCount', 0)),
                'comment_count': int(statistics.get('commentCount', 0))
            }

            return Video.model_validate(video_info)

        except HttpError as e:
            print(f"An HTTP error {e.resp.status} occurred: {e.content}")

            if e.resp.status == 403:
                error_content = e.content.decode('utf-8')
                if 'quotaExceeded' in error_content:
                    print("API quota exceeded. Try again tomorrow or request a quota increase.")
                else:
                    print("Access forbidden. Check your API key and permissions.")
            elif e.resp.status == 404:
                print("Video not found. Check the video ID.")

            raise ValueError(f"Failed to get video info: {e}")
        except Exception as e:
            raise ValueError(f"Failed to get video info: {e}")


# Lazy initialization of extractor
extractor = None

def get_extractor():
    """Get the YouTube extractor, initializing it if necessary."""
    global extractor
    if extractor is None:
        try:
            # Load environment variables when actually needed
            # Try multiple .env file locations for robustness
            load_dotenv()  # Current directory
            load_dotenv(dotenv_path=".env")  # Explicit current directory
            load_dotenv(dotenv_path="../../../.env")  # Project root
            load_dotenv(dotenv_path="../../../../.env")  # In case we're deeper

            # Debug: Print whether the API key is found
            api_key = os.getenv("YOUTUBE_DATA_API_KEY")
            if api_key:
                print(f"YouTube API key found: {api_key[:10]}...")
            else:
                print("YouTube API key not found in environment variables")
                print(f"Available env vars: {list(os.environ.keys())}")

            extractor = YouTubeCommentExtractor()
            print("YouTube Comment Extractor initialized successfully!")
        except Exception as e:
            print(f"Failed to initialize YouTube Comment Extractor: {e}")
            extractor = False  # Use False to indicate failed initialization
    return extractor if extractor is not False else None


# ----------------------------
# MCP Server
# ----------------------------


mcp = FastMCP("Youtube MCP")


@mcp.tool()
async def youtube_search_videos(
    query: str, 
    max_results: int = 10, 
    **options
    ) -> str:
    """
    Search YouTube for videos based on query and options.

    Args:
        query: Search query
        max_results: Maximum number of results to return (default: 10)
        options: Additional search options (e.g., order, publishedAfter, etc.)

    Returns:
        str: JSON string containing search results
    """
    extractor = get_extractor()
    if not extractor:
        return "YouTube Comment Extractor not initialized. Check your API key configuration."

    try:
        results = extractor.search_videos(query, max_results, **options)
        return json.dumps(results)
    except Exception as e:
        return f"Failed to search videos: {e}"


@mcp.tool()
async def youtube_load_video_data_and_comments(
    video_url: str,
    max_comments: int = 5,
    ) -> str:
    """
    Download video data and comments for a YouTube video and store them in the database.

    Args:
        video_url: YouTube video URL
        max_comments: Maximum number of comments to retrieve (default: 5). Limited to 50 to prevent memory issues.

    Returns:
        str: Success message or error message
    """
    extractor = get_extractor()
    if not extractor:
        return "YouTube Comment Extractor not initialized. Check your API key configuration."

    try:
        # Limit max_comments to prevent memory issues
        if max_comments > 50:
            logger.warning(f"Limiting max_comments from {max_comments} to 50 to prevent memory issues")
            max_comments = 50

        video = extractor.get_video_info(video_url)
        if not video:
            return f"Failed to get video info for {video_url}."

        video_create_result = await db_create_video_record(video)
        if not video_create_result:
            return f"Failed to create video record in database for {video.url}."

        comments = extractor.extract_comments(video_url, max_comments=max_comments)

        # Process comments in smaller batches to reduce memory usage
        batch_size = 20
        total_processed = 0
        for i in range(0, len(comments), batch_size):
            batch = comments[i:i + batch_size]
            validated_batch = [Comment.model_validate(comment) for comment in batch]
            await db_upsert_comments_records(validated_batch)
            total_processed += len(validated_batch)

            # Clear the batch from memory
            del validated_batch
            del batch

        # Clear comments from memory
        del comments

        return f"""Successfully loaded video and {total_processed} comments for {video.title} to the database."""

    except Exception as e:
        return f"Failed to load video and comments for {video_url}: \n\n{str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
