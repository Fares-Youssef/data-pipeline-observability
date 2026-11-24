# دليل رفع وتشغيل DEPI Weather على Microsoft Azure

هذا الدليل يفصل بين **اختبار المشروع** و**تفعيل الجمع الوطني المدفوع**. لا تفعل الجدولة الوطنية قبل إكمال قسم حدود المزود.

## 1. المتطلبات على جهازك

1. شغّل MySQL/XAMPP وتأكد أن `python weather_cli.py validate` ينجح.
2. شغّل Docker Desktop وانتظر حتى تصبح Linux Engine متاحة.
3. ثبّت Azure CLI ثم نفّذ `az login` واختر الـ subscription الصحيح.
4. نفّذ:

```powershell
cd D:\DEPI
powershell -ExecutionPolicy Bypass -File .\scripts\preflight.ps1
```

يجب أن تنجح الاختبارات، migration، وفحص قاعدة البيانات. يجب أن يظهر Azure CLI كمتاح. Docker Engine مطلوب فقط للبناء المحلي؛ يمكن أن يظل `False` عند استخدام `az acr build`.

يمكن تجاوز Docker المحلي بالكامل باستخدام `az acr build` بعد تثبيت Azure CLI؛ البناء يتم داخل Azure.

لتثبيت Azure CLI رسميًا عبر WinGet:

```powershell
winget install --exact --id Microsoft.AzureCLI
```

أغلق PowerShell وافتحه من جديد بعد التثبيت، ثم شغّل `az login`.

## 2. نسخة احتياطية محلية إلزامية

استخدم أداة XAMPP الموجودة في:

```powershell
New-Item -ItemType Directory -Force .\backups
& 'C:\xampp\mysql\bin\mysqldump.exe' --host=127.0.0.1 --port=3306 --user=<DB_USER> --password --single-transaction --routines --triggers --default-character-set=utf8mb4 --result-file="D:\DEPI\backups\depi-before-azure.sql" depi
```

سيطلب الأمر كلمة المرور بدون وضعها داخل الملف أو الكود. بعد التصدير تأكد أن الملف ليس فارغًا، واحتفظ بنسخة أخرى خارج مجلد المشروع.

## 3. قرار مزود الطقس قبل الجدولة

- التشغيل الوطني النظري: 8,992 نقطة × 3 موديلات = 26,976 موقع-موديل يوميًا قبل deduplication على grid.
- الحد المجاني المنشور لـ Open-Meteo هو 10,000 call/day ولا يتضمن SLA.
- للاختبارات استخدم `--limit`.
- للإنتاج احصل على Open-Meteo customer API key بخطة مناسبة، أو استضف Open-Meteo بنفسك.
- ضع المفتاح في Azure secret باسم `open-meteo-api-key`، وليس في الصورة أو المستودع.

التطبيق ينتقل تلقائيًا إلى `customer-api.open-meteo.com` عند ضبط `OPEN_METEO_API_KEY`.

## 4. موارد Azure المطلوبة

في الاشتراكات الجديدة، سجّل مزوّدي الموارد مرة واحدة قبل الإنشاء. التسجيل قد يستغرق عدة دقائق، ولا ينشئ موارد مدفوعة بنفسه:

```powershell
$Providers = @(
  'Microsoft.ContainerRegistry',
  'Microsoft.App',
  'Microsoft.OperationalInsights',
  'Microsoft.DBforMySQL',
  'Microsoft.Storage'
)

foreach ($Provider in $Providers) {
  az provider register --namespace $Provider --wait
}

$Providers | ForEach-Object {
  [PSCustomObject]@{
    Provider = $_
    State = (az provider show --namespace $_ --query registrationState --output tsv)
  }
} | Format-Table
```

لا تبدأ الإنشاء قبل أن تكون حالة المزوّد المطلوب `Registered`. إذا ظهر `AuthorizationFailed`، فالحساب الحالي لا يملك صلاحية `/register/action` ويلزم أن يسجله مالك الاشتراك.

أنشئ في نفس المنطقة قدر الإمكان:

1. Resource Group.
2. Azure Container Registry.
3. Log Analytics Workspace.
4. Azure Container Apps Environment.
5. Azure Database for MySQL Flexible Server، MySQL 8، مع `require_secure_transport=ON` وTLS 1.2 أو 1.3.
6. Storage Account وBlob container باسم `weather-raw`.
7. Container App للـ Flask API.
8. ثلاثة Container Apps Jobs: forecast، actuals، backfill.

استخدم Private Networking بين Container Apps وMySQL إن أمكن. إذا استخدمت public access للتجربة، قيّد firewall ولا تفتح `0.0.0.0/0`.

## 5. بناء الصورة

لا تنسخ أسماء المثال بين `<` و`>` داخل PowerShell؛ هذه علامات توضيحية وليست جزءًا من الأمر. الإعداد التالي يستخدم أسماء فعلية ومتغيرات لتجنب هذا الخطأ:

```powershell
$ResourceGroup = 'rg-depi-weather'
$Location = 'uaenorth'
$AcrName = 'depiweather56232c7d'
$ImageTag = 'v1.0.0'

az group create --name $ResourceGroup --location $Location
az acr create --resource-group $ResourceGroup --name $AcrName --sku Basic
az acr build --registry $AcrName --image "depi-weather:$ImageTag" D:\DEPI
```

لا تستخدم `latest` للإنتاج؛ استخدم tag ثابتًا مثل Git commit أو رقم إصدار.

إذا أعاد اشتراك Azure for Students الخطأ `TasksOperationsNotAllowed`، فهذا قيد على ACR Tasks في الاشتراك وليس خطأً في المشروع. اطلب تفعيل الخدمة من Azure Support أو ابنِ محليًا وادفع الصورة بنفس هوية Azure، دون تفعيل ACR Admin user:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_push_acr.ps1
```

يتطلب المسار المحلي Docker Desktop حديثًا وظهور نتيجة ناجحة من `docker info`.
السكربت يعيد محاولة `docker push` تلقائيًا عند انقطاع الشبكة، ويستفيد من الطبقات التي اكتمل رفعها. مجلد `backups` وملفات `*.sql` مستبعدة صراحة من سياق وصورة Docker.
تستخدم صورة الإنتاج `requirements-runtime.txt` فقط؛ أدوات الاختبار واستيراد الجغرافيا الكبيرة تبقى خارج الصورة لأنها غير مطلوبة لتشغيل API أو jobs.

## 6. نقل قاعدة البيانات

1. أنشئ قاعدة `depi` بترميز `utf8mb4` على Azure MySQL.
2. استورد النسخة باستخدام MySQL client واسم الخادم الكامل `<server>.mysql.database.azure.com` مع TLS.
3. اضبط أسرار التطبيق: `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME=depi`.
4. اضبط `DB_SSL_CA=/etc/ssl/certs/ca-certificates.crt` و`DB_SSL_VERIFY_CERT=true`.
5. شغّل job يدويًا مرة واحدة بالأمر `python weather_cli.py init-db`.
6. شغّل `python weather_cli.py validate` وتأكد أن `unmapped_units=0`.

قارن قبل وبعد النقل:

- `geo_unit = 8,994`
- `weather_sampling_points = 8,992`
- أسماء عربية مختارة تظهر بدون تلف.
- لا توجد parent IDs يتيمة.

## 7. متغيرات وأسرار الحاوية

أسرار: `DB_PASSWORD`, `OPEN_METEO_API_KEY`, `AZURE_STORAGE_CONNECTION_STRING`.

متغيرات غير سرية:

```text
DB_HOST=<server>.mysql.database.azure.com
DB_PORT=3306
DB_NAME=depi
DB_USER=<application-user>
DB_SSL_CA=/etc/ssl/certs/ca-certificates.crt
DB_SSL_VERIFY_CERT=true
WEATHER_BATCH_SIZE=25
WEATHER_REQUEST_INTERVAL=3
WEATHER_RATE_LIMIT_BACKOFF=30
AZURE_STORAGE_CONTAINER=weather-raw
LOG_LEVEL=INFO
```

استخدم application DB user محدود الصلاحيات بدل حساب MySQL admin.

## 8. أوامر Container Apps Jobs

Forecast job: trigger كل ساعة UTC، replica واحدة، timeout ساعتان، retry واحد:

```text
python weather_cli.py run-batch
```

`run-batch` لا يجمع إلا داخل ساعة 09:00 بتوقيت Africa/Cairo، لذلك يتعامل مع DST بدون افتراض offset ثابت.

Actuals job يوميًا:

```text
python weather_cli.py collect-actuals --lookback-days 14
```

Backfill job يوميًا:

```text
python weather_cli.py backfill-actuals --lookback-days 45
```

اضبط `parallelism=1` و`replicaCompletionCount=1`. قواعد قاعدة البيانات والـ lease تمنع عاملين من تنفيذ نفس المهمة المنطقية.

## 9. اختبار الإنتاج قبل تفعيل الجدولة

1. شغّل forecast job يدويًا بـ `--limit 5` على الصورة المنشورة.
2. أعد نفس التشغيل وتأكد أن `inserted=0` و`duplicates=150` لثلاثة موديلات وخمس نقاط.
3. اختبر رابط التطبيق:

```powershell
python scripts\verify_deployment.py https://<APP_FQDN>
```

4. افتح `/api/health`, `/api/ready`, `/api/pipeline/status`.
5. تأكد أن raw responses تظهر في Blob container.
6. أوقف job أثناء اختبار صغير، انتظر انتهاء lease، ثم أعده وتأكد أنه يستأنف بلا تكرار.
7. فعّل الجدولة فقط بعد نجاح الاختبارات ومعرفة حصة Open-Meteo الفعلية.

## 10. التنبيهات والنسخ الاحتياطي

أنشئ Azure Monitor alerts عند:

- عدم وجود forecast run ناجح خلال 30 ساعة.
- `failed` أو `partial` pipeline run.
- HTTP 429 أو أخطاء provider 5xx.
- observation lag أكبر من المسموح.
- MySQL storage أو connections أعلى من 80%.
- Container App health failures.

اضبط MySQL backup retention بين 7 و35 يومًا حسب ميزانيتك، ونفّذ restore drill دوريًا. استخدم Blob lifecycle لنقل raw archives إلى Cool/Archive، ولا تحذف forecast snapshots المعيارية.

## معنى “100% شغال” هنا

لا يمكن ضمان مزود خارجي بنسبة 100%. الجاهزية الصحيحة تعني: retries، rate-limit backoff، idempotency، leases، checkpoints، alerts، backups، وpaid SLA مناسب. النظام يحافظ على البيانات ويستأنف تلقائيًا، لكن توفر Open-Meteo وAzure يظل خدمة خارجية يجب مراقبتها.
