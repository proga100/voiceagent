# **rAIs Hybrid Chat — Final AI Logic Flow**

## Miro board - https://miro.com/app/board/uXjVH_2LRvk=/?share_link_id=722986056501

## **Summary**

rAIs avvalo foydalanuvchi murojaatining intentini aniqlab, uni umumiy agro-savol yoki kasallik/zararkunanda diagnostikasi sifatida tasniflaydi. Umumiy savollar alohida flow orqali javoblanadi, biroq dialog davomida kasallik yoki zararkunandaga oid trigger so‘zlar aniqlansa, tizim avtomatik ravishda diagnostika flow’iga o‘tadi. 

Diagnostika hech qachon faqat bitta rasmga asoslanmaydi — dialog, ekin profili, mintaqa, rivojlanish fazasi, agrotexnika, sug‘orish, oziqlanish va vizual evidence birgalikda tahlil qilinadi. Bir nechta rasm yuborilganda AI avval ularni sifat, rakurs va diagnostik qiymat bo‘yicha saralab, eng relevant rasmni chuqur tahlil qiladi. 

So‘ng barcha evidence birlashtirilib yakuniy diagnoz va uning ishonchlilik darajasi shakllantiriladi. Rasm sifati past bo‘lsa ham flow to‘xtamaydi — mavjud ma’lumot asosida past confidence bilan tahlil qilinadi va foydalanuvchidan yaxshiroq rasm so‘raladi. Diagnostika natijasida har doim mos preparatlar va Agroapteka integratsiyasi orqali tavsiyalar taqdim etiladi, shundan so‘ng agar foydalanuvchi sorasa agronom AI tomonidan berilgan diagnoz, preparatlar va dozalarni ko‘rib chiqib, yakuniy tasdiq yoki zarur tuzatishlarni beradi.

!NotebookLM Mind Map (5).png.png)

## **START**

**rAIs:**

Assalomu alaykum! Ekiningiz bo‘yicha yordam beraman. Nima bo‘yicha maslahat kerak?

├── **Kasallikni aniqlash**

├── **Zararkunandani aniqlash**

└── **Umumiy savol berish**

---

# **0. Intent Router**

rAIs avval foydalanuvchi niyatini aniqlaydi.

## **0.1. Foydalanuvchi aniq tanlov qiladi**

├── **Kasallikni aniqlash** → Diagnostic Flow’ga o‘tadi

├── **Zararkunandani aniqlash** → Diagnostic Flow’ga o‘tadi

└── **Umumiy savol berish** → General Question Flow’ga o‘tadi

---

## **0.2. Foydalanuvchi erkin matn yoki ovoz bilan yozadi**

rAIs foydalanuvchi savolini tahlil qiladi.

### **Agar savolda kasallik yoki zararkunanda triggerlari bo‘lsa**

→ **Diagnostic Flow’ga o‘tadi**

### **Agar savol umumiy agro-maslahat bo‘lsa**

→ **General Question Flow’ga o‘tadi**

### **Agar niyat noaniq bo‘lsa**

**rAIs:**

Siz ekindagi muammoni aniqlamoqchimisiz yoki umumiy agro-maslahat kerakmi?

├── Muammoni aniqlash → Diagnostic Flow

└── Umumiy savol → General Question Flow

---

# **0.3. Trigger so‘zlar logikasi**

Agar foydalanuvchi quyidagi mazmundagi so‘zlarni ishlatsa, rAIs diagnostika flow’iga qaytadi.

## **Kasallik triggerlari**

├── kasallik / касаллик

├── kasal / касал

├── dog‘ / доғ

├── sarg‘ayish / сарғайиш

├── qorayish / қорайиш

├── qo‘ng‘ir dog‘ / қўнғир доғ

├── chirish / чириш

├── so‘lish / сўлиш

├── barg qurishi

├── barg buralishi

├── poya chirishi

├── ildiz chirishi

├── zamburug‘ / замбуруғ

├── bakteriya

├── virus

└── “nima bo‘lgan?”, “nega bunday bo‘lyapti?”, “davolash kerak”

## **Zararkunanda triggerlari**

├── zararkunanda / зараркунанда

├── hasharot / ҳашарот

├── qurt / қурт

├── shira / шира

├── trips

├── kana

├── kuya

├── bit

├── lichinka

├── barg yeyilgan

├── teshiklar bor

├── hasharot ko‘rinyapti

└── “qanday dori sepaman?”, “nima bilan ishlov beraman?”

## **Trigger topilsa**

**rAIs:**

Bu savol ekindagi kasallik yoki zararkunanda bilan bog‘liq bo‘lishi mumkin. Aniqlash uchun bir nechta savol beraman.

→ **Diagnostic Flow’ga o‘tadi**

---

# **A. General Question Flow**

Bu flow foydalanuvchi kasallik yoki zararkunanda diagnozi emas, balki oddiy agro-savol berganda ishlaydi.

Misollar:

├── Qachon sug‘orish kerak?

├── Qanday o‘g‘it berish kerak?

├── Pomidorni qanday parvarish qilish kerak?

├── Bug‘doyda hosildorlikni qanday oshirish mumkin?

├── Ekish vaqti qachon?

├── Bozor narxi qanday?

└── Qaysi agrotexnik ishni qilish kerak?

---

## **A1. Savolni olish**

**rAIs:**

Savolingizni yozing yoki ovozli yuboring.

Foydalanuvchi savol beradi.

---

## **A2. Kontekst kerakmi?**

rAIs savolga javob berish uchun qo‘shimcha ma’lumot kerak yoki kerak emasligini aniqlaydi.

### **Agar kontekst yetarli bo‘lsa**

→ rAIs darhol javob beradi.

### **Agar kontekst kerak bo‘lsa**

rAIs 1–5 tagacha aniqlashtiruvchi savol beradi.

Qo‘shimcha savollar:

├── Qaysi ekin bo‘yicha so‘rayapsiz? 

├── Qaysi viloyat / tumandasiz?

├── Ekin qaysi fazada?

├── Ekish sanasi qachon bo‘lgan?

├── Oxirgi sug‘orish qachon bo‘lgan?

├── Oxirgi o‘g‘itlash qachon bo‘lgan?

└── Tuproq yoki dala holati qanday?

---

## **A3. General Answer**

rAIs javob beradi:

├── qisqa xulosa

├── amaliy tavsiya

├── nima qilish kerak

├── nimani qilmaslik kerak

├── muddat / me’yor, agar kerak bo‘lsa

└── keyingi qadam

Agar savol o‘g‘it, preparat, himoya vositasi yoki ishlov berish bilan bog‘liq bo‘lsa:

├── mos kategoriya

├── ta’sir etuvchi modda yoki mahsulot turi

├── ehtiyot chorasi

└── Agroapteka / Marketplace tavsiyasi, agar relevant bo‘lsa

---

## **A4. General Flow ichida trigger paydo bo‘lsa**

Agar foydalanuvchi umumiy savol davomida kasallik yoki zararkunanda triggerlarini ishlatsa, rAIs General Flow’dan chiqib Diagnostic Flow’ga o‘tadi.

Misol:

Foydalanuvchi:

Pomidorni qachon sug‘oray?

rAIs:

Javob beradi.

Foydalanuvchi:

Lekin barglarida sariq dog‘lar ham bor.

rAIs:

Bu kasallik yoki oziqa yetishmovchiligi belgisi bo‘lishi mumkin. Aniqlash keark.

→ **Diagnostic Flow**

---

# **B. Diagnostic Flow**

Bu flow kasallik yoki zararkunanda aniqlash uchun ishlaydi.

---

# **1. Ekin va kontekstni aniqlash**

## **1.1. Ekin profilda bormi?**

**rAIs:**

Bu ekin profilingizda bormi?

├── **Ha**

│ └── foydalanuvchi profildagi ekinni tanlaydi

│

└── **Yo‘q**

└── foydalanuvchi ekinni qo‘lda tanlaydi

---

## **1.2. Agar ekin profilda bo‘lsa**

Sistem avtomatik oladi:

├── ekin nomi

├── ekish sanasi

├── rivojlanish fazasi

├── mintaqa

├── dala ma’lumotlari

├── agrotexnika tarixi

├── ishlov berish tarixi

├── o‘g‘itlash tarixi

├── sug‘orish tarixi

└── ob-havo ma’lumotlari, agar mavjud bo‘lsa

→ **2-bosqichga o‘tadi**

---

## **1.3. Agar ekin profilda bo‘lmasa**

**rAIs:**

Keyingi safar ekinni profilingizga qo‘shib qo‘ying. Shunda faza, agrotexnika va tarixni hisobga olib, aniqroq tavsiya bera olaman.

Qo‘lda so‘raladi:

├── ekin nomi

├── viloyat / tuman

├── ekish sanasi

├── taxminiy rivojlanish fazasi

└── oxirgi agrotexnik ishlar

→ **2-bosqichga o‘tadi**

---

# **2. Dialog orqali muammoni aniqlash**

Bu bosqich majburiy. Diagnoz faqat foto asosida qilinmaydi.

## **2.1. Asosiy savollar**

rAIs so‘raydi:

├── Nima sodir bo‘lyapti?

├── Belgilar qachondan beri bor?

├── Muammo butun daladami yoki ayrim o‘simliklardami?

├── Belgilar kuchayyaptimi yoki bir xil turibdimi?

├── Oxirgi ishlov qachon va nima bilan berilgan?

├── Oxirgi sug‘orish qachon bo‘lgan?

└── Oxirgi o‘g‘itlash qachon bo‘lgan?

---

## **2.2. Kontekst yetarlimi?**

├── **Ha** → rasm bosqichiga o‘tadi

└── **Yo‘q** → qo‘shimcha savollar beradi, maksimum 10 ta

Qo‘shimcha savollar bloklari:

├── o‘simlik belgilari

├── ishlov berish tarixi

├── oziqlanish

├── sug‘orish

├── tuproq

└── oldingi ekin

→ **3-bosqichga o‘tadi**

---

# **3. Rasmlarni qabul qilish**

**rAIs:**

Aniqroq tahlil qilishim uchun bir nechta rasm yuboring: umumiy ko‘rinish, zararlangan joyning yaqindan rasmi va imkon bo‘lsa ildiz qismini ham. Yuborganingizdan song men sizga javob beraman. 

---

## **3.1. Rasm yuborildimi?**

├── **Ha** → rasmlarni validatsiya qilish

└── **Yo‘q** → kutish

---

# **4. Rasmlarni validatsiya qilish**

Sistem barcha yuborilgan rasmlarni tekshiradi.

Baholash mezonlari:

├── rasm sifati

├── yorug‘lik

├── fokus

├── simptom ko‘rinishi

├── rasm rakursi

├── takrorlanish bor-yo‘qligi

├── bitta o‘simlikmi yoki bir nechta o‘simlikmi

└── diagnoz uchun foydalilik darajasi

---

## **4.1. Agar rasm sifati past bo‘lsa**

Flow to‘xtamaydi. Qayta soraydi agar no togri rasim bolsa, yoki davom etadi

Sistem qiladi:

├── rasmni baribir ko‘rib chiqadi

├── image confidence’ni pasaytiradi

├── foydalanuvchidan yaxshiroq rasm so‘raydi

└── keyingi bosqichga o‘tadi

**rAIs:**

Rasm biroz noaniq, lekin tahlil qilib ko‘raman. Aniqroq javob uchun yana yaqinroq va yorug‘roq rasm yuborsangiz yaxshi bo‘ladi.

---

# **5. Image Selection + Per-image Analysis**

Bu bosqichda foydalanuvchi ko‘p rasm yuborishi mumkin, lekin AI diagnosis input’ga hammasi birdan yuborilmaydi.

## **5.1. Agar foydalanuvchi 1–3 ta rasm yuborsa**

Sistem har bir rasmni alohida tahlil qiladi. Va LLM ga yuboradi. 

├── Image 1 analysis

├── Image 2 analysis

└── Image 3 analysis

---

## **5.2. Agar foydalanuvchi 4–10 ta yoki undan ko‘p rasm yuborsa**

Sistem avval rasmlarni saralaydi.

AI barcha rasmlarni quyidagi mezonlar bo‘yicha tartiblaydi:

├── simptom eng aniq ko‘ringan rasm

├── zararlangan joy yaqindan tushgan rasm

├── barg usti aniq ko‘ringan rasm

├── barg osti aniq ko‘ringan rasm

├── zararkunanda izi yoki hasharot ko‘ringan rasm

├── poya / meva / ildiz zararlanishi ko‘ringan rasm

├── umumiy o‘simlik holatini ko‘rsatadigan rasm

├── sifatli, yorug‘ va fokusdagi rasm

└── takrorlanmaydigan rakurs

---

## **5.3. Maksimum 3 ta eng relevant rasm tanlanadi**

Sistem diagnoz uchun **1–3 ta eng foydali rasmni** tanlaydi.

├── agar 1 ta relevant rasm bo‘lsa → 1 ta rasm diagnosis input’ga ketadi

├── agar 2 ta relevant rasm bo‘lsa → 2 ta rasm diagnosis input’ga ketadi

├── agar 3 yoki undan ko‘p relevant rasm bo‘lsa → eng yaxshi 3 ta rasm tanlanadi

└── qolgan rasmlar case ichida saqlanadi, lekin birinchi AI diagnosis input’ga kirmaydi

---

## **5.4. Har bir tanlangan rasm alohida tahlil qilinadi**

Tanlangan har bir rasm bo‘yicha aniqlanadi:

├── ko‘rinayotgan simptomlar

├── zararlangan organ: barg, poya, meva, ildiz

├── ehtimoliy kasallik

├── ehtimoliy zararkunanda

├── oziqa yetishmovchiligi ehtimoli

├── agrotexnik stress ehtimoli

├──  confidence score

---

## **5.5. Tanlanmagan rasmlar**

Tanlanmagan rasmlar tashlab yuborilmaydi.

Ular:

├── chat ichida saqlanadi

├── agronomga yuboriladigan infoni ichiga qo‘shiladi

├── kerak bo‘lsa keyingi tekshiruvda ishlatiladi

└── birinchi AI diagnosis input’ga kiritilmaydi

→ **6-bosqichga o‘tadi**

---

---

# **6. AI javobi: diagnoz + amaliy tavsiya**

rAIs javobi quyidagilardan iborat bo‘ladi:

├── qisqa xulosa

├── ehtimoliy diagnoz

├── ishonch darajasi

├── nima uchun shunday xulosa qilingani

├── hozir nima qilish kerak

├── nimani qilmaslik kerak

├── qo‘shimcha rasm yoki ma’lumot kerak bo‘lsa, so‘rov

└── agronomga tekshirtirish tavsiyasi, agar kerak bo‘lsa

 **Preparatlar qanday shakllanadi?**

Sistem shakllantiradi:

├── ehtimoliy muammo turiga mos kategoriya

├── ta’sir etuvchi modda

├── savdo nomlari

├── mavjud AgroVet / Agroapteka mahsulotlari

├── qo‘llash shakli

├── dastlabki me’yor

├── ishlatish oralig‘i

├── xavfsizlik ogohlantirishlari

└── agronom tasdig‘i kerak bo‘lsa, belgi

**rAIs:**

Sizga kerak preparatlarni bizni Growz AgroVet dokonlarmizidan harid qilishingiz munkin. 

├── ehtimoliy muammo kategoriyasiga mos preparatlar

├── 2–3 ta variant

├── ehtiyotkorlik bilan doza

└── Marketplace tugmasi

**Belgi:**

Sun’iy intellekt tavsiyasi

---

→ 7**-bosqichga o‘tadi**

---

# **7. Agronomga tekshirtirish**

Preparatlar chiqqandan keyin foydalanuvchiga agronom validatsiyasi taklif qilinadi.

**rAIs:**

Xohlasangiz, bu javobni agronomga tekshirtirib beraman. Agronom diagnoz, preparatlar ro‘yxati va dozalarni ko‘rib chiqib, aniqroq tavsiya beradi.

---

## **7.1. Foydalanuvchi tanlovi**

├── **Agronomga yuborish**

│ └── case agronomga yuboriladi

│

├── **Hozir emas**

│ └── diagnostika yakunlanadi

---

## **7.2. Agronom javobi tayyor bo‘lganda**

Sistem:

├── push-xabarnoma yuboradi

├── AI javobi va ekspert javobini ajratib ko‘rsatadi

├── yangilangan preparatlar ro‘yxatini chiqaradi

├── Marketplace tugmasini ko‘rsatadi

**Belgi:**

Agronom tasdiqlagan javob

---

# **END**