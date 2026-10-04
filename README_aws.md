# Hướng dẫn Thực hành LAB 16: Cloud AI Environment Setup (2.5h)

Chào mừng các bạn đến với Lab 16. Trong bài thực hành này, chúng ta sẽ thiết lập một môi trường Cloud AI hoàn chỉnh trên AWS bằng cách sử dụng **Terraform** (Infrastructure as Code).

**Luồng chính (bắt buộc) của bài lab:** triển khai hạ tầng bằng Terraform, khởi động một **CPU instance nhỏ** (`t3.medium`), và huấn luyện + inference một mô hình **LightGBM** (gradient boosting) thực tế trên đó — không cần GPU, không cần xin quota, không cần tài khoản Hugging Face.

Ở cuối bài có thêm **Phụ lục (Tùy chọn — bài tập nâng cao)**: nếu bạn muốn thử sức và tài khoản của mình xin được quota GPU, bạn có thể triển khai một mô hình ngôn ngữ lớn (LLM — `google/gemma-4-E2B-it`) lên máy chủ GPU (NVIDIA T4) bằng Docker/vLLM, phục vụ qua Load Balancer. Phần này **không bắt buộc** để hoàn thành lab.

> Không có tài khoản AWS hoặc GCP? Xem [`README_other_clouds.md`](README_other_clouds.md) để làm lab này trên **Azure** hoặc **Oracle Cloud (OCI — có gói Always Free, chi phí $0)**.

---

## Phần 1: Chuẩn bị tài khoản AWS và thiết lập IAM (Least-Privilege)

Để làm việc với AWS an toàn, chúng ta không bao giờ sử dụng tài khoản Root. Thay vào đó, bạn sẽ tạo một IAM User thuộc một IAM Group với các quyền vừa đủ (least-privilege) để Terraform có thể triển khai hạ tầng.

### Bước 1.1: Truy cập AWS Console
1. Đăng nhập vào [AWS Management Console](https://console.aws.amazon.com/) bằng tài khoản Root hoặc tài khoản Admin của bạn.
2. Trên thanh tìm kiếm, gõ **IAM** và chọn dịch vụ **IAM (Identity and Access Management)**.

### Bước 1.2: Tạo IAM Group và gắn quyền (Policies)
1. Trong menu bên trái của IAM, chọn **User groups** -> click **Create group**.
2. Đặt tên nhóm: `AI-Lab-Group`.
3. Trong phần **Attach permissions policies**, bạn cần tìm và tick chọn các quyền (roles) sau. **Giải thích tại sao cần:**
   - `AmazonEC2FullAccess`: Cần thiết để Terraform tạo máy chủ ảo (Bastion Host, Compute Node), Key Pairs, và Security Groups.
   - `AmazonVPCFullAccess`: Cần thiết để Terraform tạo môi trường mạng (VPC, Subnets, Internet Gateway, NAT Gateway, Route Tables).
   - `ElasticLoadBalancingFullAccess`: Cần thiết để tạo Application Load Balancer (ALB) — hạ tầng này vẫn được triển khai trong luồng chính để bạn thực hành, dù chỉ thực sự được dùng để phục vụ API khi làm Phụ lục GPU + LLM.
   - `IAMFullAccess`: Bắt buộc vì Terraform script của chúng ta sẽ tạo một IAM Role và Instance Profile (gắn vào compute node để cấp quyền cho node nếu cần tương tác với AWS services sau này).
4. Click **Create user group**.

### Bước 1.3: Tạo IAM User và lấy Access Keys
1. Trong menu bên trái, chọn **Users** -> click **Create user**.
2. Đặt tên user: `ai-lab-user`. Click Next.
3. Chọn **Add user to group**, tick chọn nhóm `AI-Lab-Group` vừa tạo. Click Next -> **Create user**.
4. Bấm vào tên user `ai-lab-user` vừa tạo. Chuyển sang tab **Security credentials**.
5. Kéo xuống phần **Access keys**, click **Create access key**.
6. Chọn **Command Line Interface (CLI)** -> Check đồng ý -> Next -> **Create access key**.
7. **LƯU Ý:** Copy `Access key ID` và `Secret access key` lưu vào nơi an toàn. Bạn sẽ không thể xem lại Secret key sau khi đóng cửa sổ này.

> **Về GPU Quota:** Luồng chính của bài lab này **không cần** xin tăng quota GPU. Nếu bạn muốn làm thêm Phụ lục (tùy chọn) ở cuối bài để triển khai LLM trên GPU, quy trình xin quota được hướng dẫn riêng ở đó.

---

## Phần 2: Cài đặt và cấu hình môi trường Local

Trên máy tính cá nhân của bạn, mở Terminal/Command Prompt.

### Bước 2.1: Cấu hình AWS CLI
Đảm bảo bạn đã cài đặt [AWS CLI](https://aws.amazon.com/cli/). Gõ lệnh sau để cấu hình tài khoản vừa tạo:
```bash
aws configure
```
Nhập các thông tin:
- **AWS Access Key ID**: (Dán Access key ID của bạn)
- **AWS Secret Access Key**: (Dán Secret access key của bạn)
- **Default region name**: `us-east-1` (Bắt buộc dùng us-east-1 cho lab này)
- **Default output format**: `json`

### Bước 2.2: Tạo SSH Key Pair cho Terraform
Terraform cần một public key có sẵn để tạo Key Pair trên AWS (dùng để SSH vào Bastion Host và Compute Node). Trong thư mục `terraform`, chạy:
```bash
cd terraform
ssh-keygen -t rsa -b 4096 -f lab-key -N ""
```
Lệnh này tạo ra hai file: `lab-key` (private key, giữ bí mật) và `lab-key.pub` (public key, Terraform sẽ đọc file này). Cả hai đã nằm trong `.gitignore` nên sẽ không bị commit nhầm.

*(Nếu bạn định làm Phụ lục GPU + LLM ở cuối bài, phần đó cần thêm một Hugging Face Token — sẽ được hướng dẫn lấy ngay tại đó, không cần chuẩn bị trước.)*

---

## Phần 3: Triển khai Hạ tầng với Terraform

Terraform là công cụ giúp chúng ta khởi tạo hạ tầng AWS hoàn toàn tự động bằng code. Kiến trúc bao gồm:
- Mạng **Private VPC** cách ly hoàn toàn với bên ngoài.
- **Bastion Host** (t3.micro) ở Public Subnet: Dùng làm trạm trung chuyển an toàn để SSH vào Compute Node.
- **Compute Node** (`t3.medium` — 2 vCPU / 4 GB RAM) ở Private Subnet: Đây là nơi bạn sẽ cài đặt và chạy LightGBM. Instance này **mặc định là CPU**; hạ tầng đã được viết sẵn để chuyển sang GPU (`g4dn.xlarge`) nếu bạn làm Phụ lục ở cuối bài, thông qua biến `enable_gpu`.
- **NAT Gateway**: Cho phép Private Subnet tải package/dataset từ internet.
- **Application Load Balancer (ALB)**: Mở cổng 80 (HTTP), trỏ vào cổng 8000 của Compute Node. Ở luồng CPU mặc định sẽ chưa có gì lắng nghe cổng 8000 nên **health check của ALB sẽ hiển thị "unhealthy" — đây là điều bình thường**, bạn không cần xử lý gì cả trừ khi làm Phụ lục GPU + LLM.

### Bước 3.1: Khởi tạo Terraform
Di chuyển vào thư mục code Terraform (nếu bạn chưa ở đó từ Bước 2.2):
```bash
cd terraform
terraform init
```

### Bước 3.2: Triển khai (Apply)
Với luồng CPU mặc định, bạn **không cần khai báo biến môi trường nào cả** — chỉ cần chạy:
```bash
terraform apply
```
Gõ `yes` khi được hỏi. Quá trình này sẽ mất khoảng **10 đến 15 phút** (phần lớn thời gian là để khởi tạo NAT Gateway).

*Mẹo: Các bạn hãy bắt đầu bấm giờ (benchmark) từ lúc gõ `yes` ở bước này nhé!*

---

## Phần 4: Kết nối và Huấn luyện mô hình LightGBM trên CPU Node

Khi `terraform apply` chạy xong, màn hình terminal sẽ in ra các thông số quan trọng (Outputs). Trông sẽ giống thế này:
```text
Outputs:

alb_dns_name = "ai-inference-alb-xxxxxx.us-east-1.elb.amazonaws.com"
bastion_public_ip = "100.x.x.x"
endpoint_url = "http://ai-inference-alb-xxxxxx.us-east-1.elb.amazonaws.com/v1/completions"
gpu_private_ip = "10.0.1x.x"
```
`gpu_private_ip` chính là IP private của Compute Node (CPU) bạn vừa tạo — tên biến giữ nguyên từ hạ tầng dùng chung với phần GPU tùy chọn. `endpoint_url`/`alb_dns_name` chỉ có ý nghĩa nếu bạn làm Phụ lục GPU + LLM ở cuối bài; ở luồng CPU bạn có thể bỏ qua hai giá trị này.

### Bước 4.1: SSH vào Compute Node qua Bastion Host
```bash
# SSH vào Bastion Host
ssh -i lab-key ubuntu@<BASTION_PUBLIC_IP>

# Từ Bastion, SSH vào Compute Node (dùng IP private ở trên)
ssh ubuntu@<CPU_PRIVATE_IP>
```

### Bước 4.2: Kiểm tra môi trường ML
Terraform đã tự động cài sẵn Python, LightGBM, scikit-learn, pandas, numpy và Kaggle CLI cho bạn qua `user_data`. Đợi khoảng 1-2 phút sau khi instance chạy xong rồi kiểm tra:
```bash
python3 -c "import lightgbm, sklearn, pandas, numpy; print('OK')"
```
Nếu chưa thấy `OK` (do user_data còn đang chạy), xem log cài đặt bằng:
```bash
sudo tail -f /var/log/user-data.log
```

### Bước 4.3: Tải Dataset từ Kaggle

Chúng ta sẽ dùng **Credit Card Fraud Detection** — bộ dữ liệu chuẩn cho benchmark ML với 284,807 giao dịch thực.

**Lấy Kaggle API Key:**
1. Đăng nhập [kaggle.com](https://www.kaggle.com) -> **Settings** -> **API** -> **Create New Token** -> tải về `kaggle.json`.
2. Copy nội dung file vào máy EC2:

```bash
mkdir -p ~/.kaggle
# Tạo file credentials (thay YOUR_USERNAME và YOUR_KEY):
cat > ~/.kaggle/kaggle.json << 'EOF'
{"username": "YOUR_KAGGLE_USERNAME", "key": "YOUR_KAGGLE_API_KEY"}
EOF
chmod 600 ~/.kaggle/kaggle.json

mkdir -p ~/ml-benchmark
kaggle datasets download -d mlg-ulb/creditcardfraud --unzip -p ~/ml-benchmark/
```

### Bước 4.4: Huấn luyện và Inference với LightGBM

Trong thư mục `terraform/`, chạy script benchmark trên dataset đã tải ở bước 4.3:

```bash
python3 benchmark.py --data ~/ml-benchmark/creditcard.csv
```

Script cũng tự tìm `creditcard.csv` trong thư mục hiện tại, thư mục chứa script
hoặc `~/ml-benchmark/` nếu không truyền `--data`. Kết quả được ghi vào
`terraform/benchmark_result.json` theo mặc định; có thể đổi vị trí bằng
`--output /path/to/benchmark_result.json`.

Script thực hiện:
1. Load dataset và tách tập train/test.
2. Huấn luyện `LGBMClassifier` để phát hiện gian lận, dùng validation split riêng
   để early stopping và giữ test split cho đánh giá cuối.
3. Đo thời gian load data và thời gian training.
4. Đánh giá model trên tập test: AUC-ROC, Accuracy, F1-Score, Precision, Recall.
5. Đo **inference latency** (dự đoán 1 dòng) và **inference throughput** (dự đoán 1000 dòng).
6. Ghi toàn bộ kết quả ra file `benchmark_result.json`.

Chạy script và điền kết quả vào bảng:

| Metric | Kết quả |
|---|---|
| Thời gian load data | 2.5709 |
| Thời gian training | 2.1635 |
| Best iteration | 11 |
| AUC-ROC | 0.962205 |
| Accuracy | 0.990432 |
| F1-Score | 0.242003 |
| Precision | 0.140097 |
| Recall | 0.140097 |
| Inference latency (1 row) | 1.2036 ms |
| Inference throughput (1000 rows) | 586205.27 rows/second |

---

## Phần 5: Kiểm tra Tài nguyên và Chi phí

Ngay sau khi chạy xong benchmark, hãy kiểm tra và chụp lại các chỉ số sau (không cần đợi 1 giờ):

### 5.1: CPU, RAM, Network usage (trên Compute Node, qua SSH)
```bash
# CPU usage theo thời gian thực (nhấn q để thoát)
top

# RAM usage
free -h

# Network usage (số byte/gói tin đã gửi-nhận qua interface)
ip -s link
```
Bạn cũng có thể xem các chỉ số này trên **EC2 Console -> Instances -> chọn Compute Node -> tab Monitoring** (biểu đồ `CPUUtilization`, `NetworkIn`, `NetworkOut`).

### 5.2: Billing / Cost Dashboard
1. Vào [AWS Billing Console](https://console.aws.amazon.com/billing/) -> **Bills** hoặc **Cost Explorer**.
2. Chọn ngày hôm nay để xem chi phí hiện tại.
3. Chụp màn hình thể hiện các dịch vụ đang phát sinh chi phí (EC2, NAT Gateway).

**Ước tính chi phí/giờ (us-east-1) cho luồng CPU mặc định:**

| Dịch vụ | Instance/Loại | Chi phí/giờ |
|---|---|---|
| EC2 — Compute Node | `t3.medium` | ~$0.0416 |
| EC2 — Bastion | `t3.micro` | ~$0.010 |
| NAT Gateway | (mỗi AZ) | ~$0.045 + data |
| ALB | Application Load Balancer | ~$0.008 |
| **Tổng ước tính** | | **~$0.10/giờ** |

### 5.3: GPU usage (Tùy chọn)
Chỉ áp dụng nếu bạn đã làm Phụ lục GPU + LLM ở cuối bài. Kiểm tra bằng lệnh `nvidia-smi` trên Compute Node (chi tiết ở Phụ lục).

---

## Phần 6: Tiêu chí nộp bài (Deliverables)

Để hoàn thành Lab 16, sinh viên cần thu thập và nộp các kết quả sau:
1. **Screenshot terminal** chạy `python3 benchmark.py` với toàn bộ output kết quả.
2. **File `benchmark_result.json`** chứa metrics đầy đủ (training time, AUC, inference latency, throughput...).
3. **Screenshot tài nguyên**: `top`/`free -h` (hoặc EC2 Monitoring tab) thể hiện CPU/RAM/Network usage.
4. **Screenshot AWS Billing/Cost Dashboard** thể hiện các dịch vụ đang phát sinh chi phí (EC2, NAT Gateway).
5. **Mã nguồn:** Nén thư mục `terraform/` đã chạy thành công.
6. **Báo cáo ngắn** (5-10 dòng): nhận xét về kết quả training time, AUC, inference speed trên CPU.

*(Nếu bạn làm thêm Phụ lục GPU + LLM, có thêm các mục nộp bài riêng — xem cuối Phụ lục.)*

---

## Phần 7: Dọn dẹp tài nguyên (CỰC KỲ QUAN TRỌNG)

NAT Gateway tính phí theo giờ ngay cả khi dùng CPU instance nhỏ. Ngay sau khi test thành công và chụp ảnh nộp bài, bạn **BẮT BUỘC** phải xóa toàn bộ tài nguyên để tránh mất tiền.

Chạy lệnh sau trong thư mục `terraform`:
```bash
terraform destroy
```
Gõ `yes` khi được hỏi. Quá trình xóa sẽ mất khoảng 5 phút. Hãy đợi đến khi terminal báo `Destroy complete!` để chắc chắn mọi thứ đã bị xóa.

---

## Phụ lục (Tùy chọn — Bài tập nâng cao): Triển khai GPU + LLM Inference (vLLM)

> Phần này **không bắt buộc**. Nó chỉ dành cho các bạn muốn thử sức thêm và có tài khoản AWS xin được quota GPU. Việc hoàn thành hay không hoàn thành phần này **không ảnh hưởng** đến việc đạt yêu cầu của Lab 16 (Phần 1-7 ở trên).

Mục tiêu: triển khai mô hình ngôn ngữ lớn (LLM — `google/gemma-4-E2B-it`) lên một máy chủ GPU (NVIDIA T4) nằm an toàn trong Private VPC, cung cấp API truy cập ra bên ngoài qua Load Balancer, dùng Docker/vLLM.

### A.1: Tăng hạn mức vCPU cho GPU (Rất quan trọng)
Theo mặc định, AWS khóa hạn mức sử dụng máy chủ GPU của các tài khoản mới ở mức 0 vCPU để bảo mật. Bạn cần mở khóa để chạy được instance `g4dn.xlarge` (cần 4 vCPU).
1. Trên thanh tìm kiếm của AWS Console, gõ **Service Quotas** và chọn nó.
2. Menu trái chọn **AWS services** -> tìm và chọn **Amazon Elastic Compute Cloud (Amazon EC2)**.
3. Ở ô tìm kiếm của Quotas, gõ `Running On-Demand G and VT instances`.
4. Chọn nó và click **Request quota increase**.
5. Nhập số **4** (tương đương 4 vCPU cho 1 máy `g4dn.xlarge`).
*Lưu ý: AWS có thể mất từ vài phút đến vài giờ để duyệt yêu cầu này. Nếu bị từ chối hoặc chưa duyệt kịp, bạn hoàn toàn có thể bỏ qua phần Phụ lục này — nó là tùy chọn.*

### A.2: Lấy Hugging Face Token
Mô hình `google/gemma-4-E2B-it` là một mô hình bị giới hạn (gated model). Bạn cần cấp quyền truy cập cho Terraform.
1. Đăng nhập [Hugging Face](https://huggingface.co/).
2. Vào trang của model [google/gemma-4-E2B-it](https://huggingface.co/google/gemma-4-E2B-it) và đồng ý với điều khoản (Accept license).
3. Vào **Settings** -> **Access Tokens** -> Tạo một token (quyền Read) và copy lại.

### A.3: Chuyển hạ tầng sang GPU + vLLM
Hạ tầng Terraform đã hỗ trợ sẵn việc bật GPU thông qua biến `enable_gpu` — bạn không cần sửa code, chỉ cần khai báo biến môi trường:
```bash
cd terraform
export TF_VAR_enable_gpu=true
export TF_VAR_hf_token="<DÁN_TOKEN_HUGGING_FACE_CỦA_BẠN_VÀO_ĐÂY>"
terraform apply
```
Gõ `yes` khi được hỏi. Terraform sẽ thay thế Compute Node CPU hiện tại bằng một node GPU (`g4dn.xlarge`, Deep Learning AMI) chạy Docker/vLLM.

> **Quan trọng:** Nếu bạn đã destroy hạ tầng CPU ở Phần 7, việc apply lại với `enable_gpu=true` sẽ tạo toàn bộ hạ tầng từ đầu (~10-15 phút). Nếu hạ tầng CPU vẫn đang chạy, Terraform sẽ chỉ thay thế Compute Node, các phần còn lại (VPC, Bastion, ALB...) được giữ nguyên.

### A.4: Kiểm tra AI Endpoint (Inference)
Sau khi apply xong, GPU Node vẫn đang ngầm tải Docker image (vLLM) và model weights (~vài GB) từ Hugging Face. **Bạn cần đợi thêm 5-10 phút** để model sẵn sàng.

Thay thế URL của ALB bạn nhận được (output `alb_dns_name`) vào lệnh dưới đây và chạy thử:
```bash
curl -X POST http://<THAY_BẰNG_ALB_DNS_NAME_CỦA_BẠN>/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "google/gemma-4-E2B-it",
    "messages": [
      {"role": "system", "content": "Bạn là một trợ lý AI hữu ích."},
      {"role": "user", "content": "Hãy giải thích Bastion Host trong AWS là gì?"}
    ],
    "max_tokens": 150
  }'
```
Nếu nhận được câu trả lời từ AI, chúc mừng bạn đã triển khai thành công! Hãy ghi lại tổng thời gian (Cold start time) từ lúc chạy `terraform apply` đến lúc nhận được API response đầu tiên.

### A.5: Kiểm tra GPU usage
SSH vào GPU Node qua Bastion (như Bước 4.1) rồi chạy:
```bash
nvidia-smi
```
để xem GPU utilization, VRAM usage, và tiến trình Docker/vLLM đang chạy.

### A.6: Tiêu chí nộp bài (Phụ lục GPU + LLM)
Nếu làm thêm phần này, nộp bổ sung các mục sau:
1. **Screenshot API gọi thành công:** lệnh curl và câu trả lời của AI.
2. **Report Cold Start Time:** tổng thời gian triển khai (Mục tiêu: < 15 phút cho instance T4).
3. **Screenshot `nvidia-smi`** thể hiện GPU usage khi model đang chạy.

### A.7: Dọn dẹp
Dù kết thúc ở CPU hay đã chuyển sang GPU, bước dọn dẹp vẫn là chạy `terraform destroy` trong thư mục `terraform` (xem Phần 7). GPU EC2 (`g4dn.xlarge`) tính phí theo giờ và **đắt hơn đáng kể** so với CPU node — đừng quên destroy ngay sau khi test xong.
