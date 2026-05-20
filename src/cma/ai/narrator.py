import json
import logging

from openai import AsyncOpenAI

from cma.config import settings

logger = logging.getLogger(__name__)

NARRATOR_SYSTEM_PROMPT = """أنت محلل سوق عقاري سعودي محترف. مهمتك كتابة ملخص تنفيذي احترافي لتحليل السوق المقارن (CMA) باللغة العربية.

اكتب تقريراً يتضمن:
1. **نظرة عامة على السوق**: وصف موجز لسوق الحي المستهدف والأسعار السائدة
2. **تحليل الأسعار**: تحليل نطاق الأسعار وسعر المتر المربع ومقارنة بالسوق
3. **تقييم أفضل الخيارات**: لماذا تم ترشيح العقارات الأعلى تقييماً
4. **التوصية النهائية**: توصية واضحة مع التبرير

أسلوب الكتابة:
- احترافي ومباشر
- استخدم أرقاماً محددة وإحصائيات
- لا تستخدم عبارات ترويجية مبالغة
- اجعل التقرير مفيداً لوكيل عقاري يعرضه على عميله
- الطول: 300-500 كلمة"""


def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=settings.openrouter_api_key,
    )


class ReportNarrator:
    def __init__(self):
        self.client = _get_client()
        self.model = settings.openrouter_model

    async def narrate(
        self,
        kpis: dict,
        top_listings: list[dict],
        city: str,
        neighborhood: str,
        offer_type: str,
        property_type: str,
    ) -> str:
        safe_listings = []
        for item in top_listings[:5]:
            safe_listings.append({
                "السعر": item.get("السعر"),
                "الحي": item.get("الحي"),
                "المساحة_متر": item.get("المساحة_متر"),
                "السعر_للمتر": item.get("السعر_للمتر"),
                "غرف_النوم": item.get("غرف_النوم"),
                "المميزات": item.get("المميزات", []),
                "تقييم_القيمة": item.get("تقييم_القيمة"),
                "ترتيب_التوصية": item.get("ترتيب_التوصية"),
            })

        context = {
            "المدينة": city,
            "الحي": neighborhood,
            "نوع_العرض": offer_type,
            "نوع_الوحدة": property_type,
            "المؤشرات": kpis,
            "أفضل_العقارات": safe_listings,
        }

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                max_tokens=2000,
                messages=[
                    {"role": "system", "content": NARRATOR_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            "اكتب ملخصاً تنفيذياً لتحليل السوق المقارن بناءً على البيانات التالية:\n\n"
                            + json.dumps(context, ensure_ascii=False, indent=2)
                        ),
                    },
                ],
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Narrator OpenRouter error: {e}")
            return "تعذر إنشاء الملخص التنفيذي. يرجى مراجعة البيانات الإحصائية أدناه."
