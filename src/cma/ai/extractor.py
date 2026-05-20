import asyncio
import json
import logging

from openai import AsyncOpenAI

from cma.config import settings

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = """أنت متخصص في استخراج بيانات العقارات من السوق السعودي.
مهمتك تحويل محتوى صفحات الإعلانات العقارية العربية إلى بيانات منظمة بصيغة JSON.

يجب أن يتطابق الناتج مع هذا المخطط بالضبط:
{
    "نوع_الوحدة": "شقة|فيلا|دور|أرض|عمارة|استراحة|محل|مكتب",
    "نوع_العرض": "إيجار|بيع",
    "السعر": <رقم>,
    "العملة": "ريال سعودي",
    "الشارع": "<اسم الشارع أو null>",
    "الحي": "<اسم الحي>",
    "المنطقة": "<اسم المنطقة أو null>",
    "المدينة": "<اسم المدينة>",
    "المساحة_متر": <رقم أو null>,
    "السعر_للمتر": <رقم محسوب: السعر / المساحة_متر أو null>,
    "تقييم_القيمة": null,
    "الفئة": "عوائل|أفراد|null",
    "غرف_النوم": <رقم أو null>,
    "الصالات": <رقم أو null>,
    "دورات_المياه": <رقم أو null>,
    "الدور": <رقم أو null>,
    "عمر_العقار": <رقم بالسنوات أو null>,
    "المميزات": ["قائمة المميزات"],
    "رقم_التواصل": "<رقم الهاتف أو null>",
    "تاريخ_الإعلان": "<تاريخ أو null>",
    "رابط_الإعلان": "<الرابط>",
    "ترتيب_التوصية": null
}

قواعد الاستخراج:
- استخرج الأرقام العربية (٣) والإنجليزية (3) بشكل صحيح
- "٣ غرف نوم وصالة" تعني غرف_النوم: 3، الصالات: 1
- احسب السعر_للمتر إذا توفر السعر والمساحة
- اترك الحقل null إذا لم تجد المعلومة - لا تخمن
- استخرج كل المميزات المذكورة (مكيفات، مصعد، موقف، مسبح، حارس، إلخ)
- أرجع JSON فقط بدون أي نص إضافي"""


def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=settings.openrouter_api_key,
    )


class ListingExtractor:
    def __init__(self):
        self.client = _get_client()
        self.model = settings.openrouter_model

    async def extract(self, raw_html: str, listing_url: str) -> dict | None:
        content = self._clean_html(raw_html)
        if not content:
            return None

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                max_tokens=2000,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"استخرج بيانات هذا الإعلان العقاري.\n"
                            f"الرابط: {listing_url}\n\n"
                            f"محتوى الصفحة:\n{content[:6000]}"
                        ),
                    },
                ],
            )

            text = response.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                text = text.rsplit("```", 1)[0]

            data = json.loads(text)
            data["رابط_الإعلان"] = listing_url

            if data.get("السعر") and data.get("المساحة_متر"):
                data["السعر_للمتر"] = round(data["السعر"] / data["المساحة_متر"], 2)

            return data

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON from model response for {listing_url}: {e}")
            return None
        except Exception as e:
            logger.error(f"OpenRouter API error for {listing_url}: {e}")
            return None

    async def extract_batch(
        self, listings: list[tuple[str, str]], concurrency: int = 5
    ) -> list[dict]:
        semaphore = asyncio.Semaphore(concurrency)
        results = []

        async def _extract_one(html: str, url: str):
            async with semaphore:
                await asyncio.sleep(0.3)
                result = await self.extract(html, url)
                if result:
                    results.append(result)

        tasks = [_extract_one(html, url) for html, url in listings]
        await asyncio.gather(*tasks)
        return results

    def _clean_html(self, html: str) -> str:
        import re

        # Remove noisy tag blocks entirely
        for tag in ["script", "style", "nav", "footer", "header", "noscript", "iframe", "svg"]:
            html = re.sub(
                rf"<{tag}[^>]*>.*?</{tag}>", "", html, flags=re.DOTALL | re.IGNORECASE
            )

        # Strip remaining HTML tags
        text = re.sub(r"<[^>]+>", " ", html)

        # Remove raw URLs
        text = re.sub(r"https?://\S+", "", text)

        # Remove long base64 / token strings (common in Next.js pages)
        text = re.sub(r"[A-Za-z0-9+/=_\-]{60,}", "", text)

        # Keep only lines that contain Arabic characters or digits
        lines = text.splitlines()
        arabic_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if re.search(r"[\u0600-\u06FF\d]", line):
                arabic_lines.append(line)

        text = "\n".join(arabic_lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()
