import unittest
from unittest.mock import patch

from backend.app import vector_store


class SearchDocumentsTopKTests(unittest.TestCase):
    def test_search_documents_can_return_five_results(self):
        canonical_document_id = "abcdef01-2345-4678-9abc-def012345678"
        documents = [f"chunk {index}" for index in range(5)]
        metadatas = [
            {"page": index + 1, "source": "sample.pdf"}
            for index in range(5)
        ]
        distances = [0.1 + index * 0.1 for index in range(5)]
        query_results = {
            "documents": [documents],
            "metadatas": [metadatas],
            "distances": [distances],
        }

        with patch.object(vector_store, "collection") as mock_collection:
            mock_collection.query.return_value = query_results
            results = vector_store.search_documents(
                [0.0], top_k=5, document_id=canonical_document_id
            )

        self.assertEqual(len(results), 5)
        self.assertEqual(
            [result["text"] for result in results],
            documents,
        )
        mock_collection.query.assert_called_once_with(
            query_embeddings=[[0.0]],
            n_results=5,
            where={"document_id": canonical_document_id},
            include=["documents", "metadatas", "distances"],
        )


class SearchDocumentsDistanceTests(unittest.TestCase):
    def test_optional_threshold_filters_distances_and_preserves_metadata(self):
        canonical_document_id = "abcdef01-2345-4678-9abc-def012345678"
        query_results = {
            "documents": [["below", "equal", "above", "missing", "absent"]],
            "metadatas": [[
                {"page": index + 1, "source": f"sample-{index}.pdf", "document_id": canonical_document_id}
                for index in range(5)
            ]],
            "distances": [[0.25, 0.5, 0.75, None]],
        }
        for threshold, retained in [(None, range(5)), (0.5, range(2))]:
            with self.subTest(threshold=threshold):
                with patch.object(vector_store, "collection") as mock_collection:
                    mock_collection.query.return_value = query_results
                    results = vector_store.search_documents(
                        [0.0], top_k=5, document_id=canonical_document_id,
                        max_distance=threshold,
                    )
                self.assertEqual(results, [
                    {
                        "text": query_results["documents"][0][index],
                        "page": index + 1,
                        "source": f"sample-{index}.pdf",
                        "distance": query_results["distances"][0][index] if index < 4 else None,
                    }
                    for index in retained
                ])
                mock_collection.query.assert_called_once_with(
                    query_embeddings=[[0.0]], n_results=5,
                    where={"document_id": canonical_document_id},
                    include=["documents", "metadatas", "distances"],
                )


if __name__ == "__main__":
    unittest.main()
