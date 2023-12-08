import cohere
import os 
from dotenv import load_dotenv
import json
import uuid
import hnswlib
from typing import List, Dict
from unstructured.partition.html import partition_html
from unstructured.chunking.title import chunk_by_title

class Documents:

    def __init__(self, sources: List[Dict[str, str]], co):
        self.sources = sources
        self.docs = []
        self.docs_embs = []
        self.retrieve_top_k = 500
        self.rerank_top_k = 100
        self.co = co
        self.load()
        self.embed()
        self.index()

    def load(self, max_articles = 500):
        """
        load pubmed data
        Note: max articles = 5 just for testing
        """
        doc = None
        with open("datasets/xray_articles.json", "r", encoding='utf-8') as f:
            doc = json.load(f)

        for article in doc:
            if max_articles == 0:
                break
            article_dict = []
            for text in article["FullText"]:
                article_dict.append(
                    {
                        "title": article["Title"],
                        "text": text,
                    }
                )
            self.docs.extend(article_dict)
            max_articles -= 1
        

    def embed(self) -> None:
        """
        Embeds the documents using the Cohere API.
        """
        print("Embedding documents...")

        batch_size = 90
        self.docs_len = len(self.docs)

        for i in range(0, self.docs_len, batch_size):
            batch = self.docs[i : min(i + batch_size, self.docs_len)]
            texts = [item["text"] for item in batch]
            docs_embs_batch = self.co.embed(
		              texts=texts,
                      model="embed-english-v3.0",
                      input_type="search_document"
	 		).embeddings
            self.docs_embs.extend(docs_embs_batch)

    def index(self) -> None:
        """
        Indexes the documents for efficient retrieval.
        """
        print("Indexing documents...")

        self.index = hnswlib.Index(space="ip", dim=1024)
        self.index.init_index(max_elements=self.docs_len, ef_construction=512, M=64)
        self.index.add_items(self.docs_embs, list(range(len(self.docs_embs))))

        print(f"Indexing complete with {self.index.get_current_count()} documents.")

    def retrieve(self, query: str) -> List[Dict[str, str]]:
        """
        Retrieves documents based on the given query.

        Parameters:
        query (str): The query to retrieve documents for.

        Returns:
        List[Dict[str, str]]: A list of dictionaries representing the retrieved  documents, with 'title', 'snippet', and 'url' keys.
        """
        docs_retrieved = []
        query_emb = self.co.embed(
                    texts=[query],
                    model="embed-english-v3.0",
                    input_type="search_query"
                    ).embeddings				    

        doc_ids = self.index.knn_query(query_emb, k=self.retrieve_top_k)[0][0]

        docs_to_rerank = []
        for doc_id in doc_ids:
            docs_to_rerank.append(self.docs[doc_id]["text"])

        rerank_results = self.co.rerank(
            query=query,
            documents=docs_to_rerank,
            top_n=self.rerank_top_k,
            model="rerank-english-v2.0",
        )

        doc_ids_reranked = []
        for result in rerank_results:
            doc_ids_reranked.append(doc_ids[result.index])

        for doc_id in doc_ids_reranked:
            docs_retrieved.append(
                {
                    "title": self.docs[doc_id]["title"],
                    "text": self.docs[doc_id]["text"],
                }
            )

        return docs_retrieved
    

class Chatbot:
    def __init__(self, docs: Documents, co):
        self.co = co
        self.conversation_id = str(uuid.uuid4())
        self.docs = docs
    
    def generate_response(self, message: str, chat_history = None, max_tokens = 500):
        """
        Generates a response to the user's message.

        Parameters:
        message (str): The user's message.

        Yields:
        Event: A response event generated by the chatbot.

        Returns:
        List[Dict[str, str]]: A list of dictionaries representing the retrieved documents.

        """

        # Generate search queries (if any)
        response = self.co.chat(message=message, search_queries_only=True)

        # If there are search queries, retrieve documents and respond
        if response.search_queries:
            print("Retrieving information...")

            documents = self.retrieve_docs(response)

            if chat_history is not None:
                response = self.co.chat(
                    message=message,
                    documents=documents,
                    conversation_id=self.conversation_id,
                    stream=True,
                    chat_history=chat_history,
                    max_tokens=max_tokens,
                )
            else:
                response = self.co.chat(
                    message=message,
                    documents=documents,
                    conversation_id=self.conversation_id,
                    stream=True,
                    max_tokens=max_tokens,
                )

        # If there is no search query, directly respond
        else:
            if chat_history is not None:
                response = self.co.chat(
                    message=message, 
                    conversation_id=self.conversation_id, 
                    stream=True,
                    chat_history=chat_history,
                    max_tokens=max_tokens,
                )
            
            else:
                response = self.co.chat(
                    message=message, 
                    conversation_id=self.conversation_id, 
                    stream=True,
                    max_tokens=max_tokens,
                )

        return response
    
    def retrieve_docs(self, response) -> List[Dict[str, str]]:
            """
            Retrieves documents based on the search queries in the response.

            Parameters:
            response: The response object containing search queries.

            Returns:
            List[Dict[str, str]]: A list of dictionaries representing the retrieved documents.

            """
            # Get the query(s)
            queries = []
            for search_query in response.search_queries:
                queries.append(search_query["text"])

            # Retrieve documents for each query
            retrieved_docs = []
            for query in queries:
                retrieved_docs.extend(self.docs.retrieve(query))

            return retrieved_docs
    



if __name__ == "__main__":
    chat_history=[
        {"role": "USER", "message": "I have had a fracture"},
        {"role": "CHATBOT", "message": "I am here to assist you. Can you tell me more about your fracture?"},
      ]
    message="I fell off while biking and broke my wrist and my upper arm."
    load_dotenv()
    cohere_api_key = os.getenv('COHERE_API_KEY')
    co = cohere.Client(cohere_api_key)
    docs = Documents(None, co)
    cohere = Chatbot(docs, co)
    response = cohere.generate_response(message, chat_history, max_tokens=500)
    print(response)

    while True:
        # Get the user message
        message = input("User: ")

        # Typing "quit" ends the conversation
        if message.lower() == "quit" or message.lower() == "q":
            print("Ending chat.")
            break
        else:
            response = cohere.generate_response(message, max_tokens=500)
            print(response.text)


    