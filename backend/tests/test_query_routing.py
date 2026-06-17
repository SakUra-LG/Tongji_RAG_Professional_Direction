import unittest

from app.components import QueryIntentRouter
from app.dto import Document
from app.pipelines import AutoPipeline


class QueryIntentRouterTests(unittest.TestCase):
    routing_context = {
        "role": "student",
        "dept_id": "CS",
        "college_name": "计算机系",
    }

    def test_owned_college_attribute_is_campus_knowledge(self):
        route = QueryIntentRouter.analyze_fallback(
            "我的学院成立于哪一年？",
            self.routing_context,
        )
        self.assertEqual(route.intent, "campus_knowledge")
        self.assertIn("同济大学计算机科学与技术学院", route.rewritten_query)
        self.assertIsNone(route.personal_field)

    def test_owned_college_name_is_personal_fact(self):
        route = QueryIntentRouter.analyze_fallback(
            "我的学院是什么？",
            self.routing_context,
        )
        self.assertEqual(route.intent, "personal_fact")
        self.assertEqual(route.personal_field, "college")

    def test_gpa_value_is_personal_fact(self):
        route = QueryIntentRouter.analyze_fallback(
            "我的绩点是多少？",
            self.routing_context,
        )
        self.assertEqual(route.intent, "personal_fact")
        self.assertEqual(route.personal_field, "gpa")

    def test_gpa_procedure_is_not_personal_fact(self):
        route = QueryIntentRouter.analyze_fallback(
            "我如何查询绩点？",
            self.routing_context,
        )
        self.assertEqual(route.intent, "procedure")

    def test_short_college_query_requests_clarification(self):
        route = QueryIntentRouter.analyze_fallback(
            "学院",
            self.routing_context,
        )
        self.assertEqual(route.intent, "clarification")

    def test_document_fallback_extracts_exact_foundation_sentence(self):
        answer = AutoPipeline._format_document_fallback(
            "同济大学计算机科学与技术学院成立于哪一年",
            [
                Document(
                    id="1",
                    content=(
                        "2005年，获得一级学科博士学位授予权。\n"
                        "2024年7月19日，同济大学成立计算机科学与技术学院，"
                        "由原计算机科学与技术系和原软件学院组建而成。"
                    ),
                    score=1.0,
                    source="计算机学院官网",
                )
            ],
        )
        self.assertIn("2024年7月19日", answer)
        self.assertIn("计算机学院官网", answer)


if __name__ == "__main__":
    unittest.main()
