[![SEASON UPGRADE 25, 26, WC18, SERIE A](docs/season-upgrade-25-26-wc18.svg)](https://www.mediafire.com/file/ettm1y9u3s0r3t2/FO3ID-Season-25-26-LIVE-WC18-SerieA-client.zip/file)

# FO3ID Private Server

[English](README.md) | [Bahasa Indonesia](README.id.md) | [简体中文](README.zh-CN.md) | [ไทย](README.th.md) | **Tiếng Việt** | [한국어](README.ko.md)

> Bản dịch của [README.md](README.md). Nếu có chỗ khác nhau, README.md là bản mới nhất.

**Mới:** thẻ 25 LIVE và 26 LIVE của FIFA Online 4 thành hai mùa mới (chỉ số tính là +0, đặc tính và biểu tượng mùa của FO4), 26 LIVE là mùa mặc định, và WC18: 300 cầu thủ hay nhất trong danh sách đội tuyển World Cup 2018 (ở +1 mỗi thẻ bằng chỉ số cuối giải - 5). Xem `tools/fo4_season.py`.

Máy chủ riêng chạy cục bộ cho **FIFA Online 3 Indonesia** (Garena, client build 654420), viết bằng
Python. Garena đã đóng máy chủ chính thức ngày 2020-06-30; dự án này dựng lại phần backend mà client
kết nối tới, để có thể chơi game offline trên một máy tính.

> Không liên quan đến EA, Spearhead hay Garena. Kho mã này **không chứa file game nào** (không có
> client, cơ sở dữ liệu, tài nguyên hay nhạc); bạn cần tự cài FO3 ID. Dự án **không** kèm theo, không
> sửa đổi và không vượt qua hệ thống chống gian lận của client (XIGNCODE3 / HackShield); việc đó nằm
> ngoài phạm vi. Làm ra để bảo tồn game và nghiên cứu.

Giao diện game là tiếng Indonesia. Các tên như Liga, Cup, Market, Tukar, Tim inti, Tim Cadangan,
Item saya, Gudang bên dưới là tên màn hình trong game, được giữ nguyên để dễ tìm trong game.

## Những gì đã chạy được

Đã thử trong client thật (giao diện tiếng Indonesia):

- Đăng nhập → chọn / tạo HLV (chọn CLB trong 501 CLB có bản quyền) → sảnh (FREEROAM).
- **Single → Liga** (cả mùa giải: lịch thi đấu, trận đấu, bảng xếp hạng, phần thưởng, thăng hạng) và
  **Cup** (bốc thăm, vòng bảng, loại trực tiếp, hiệp phụ, luân lưu).
- **Market** (mua / bán; giá thẻ theo chỉ số và cấp thẻ: trung vị giá trên fifaaddict của mọi thẻ cùng
  chỉ số), **Upgrade** (cấp +1…+10), giao dịch cầu thủ **Tukar** và **Tukar Scout** (mọi thẻ trên
  Market, không chỉ thẻ có bản quyền trong khu vực), Tim inti (đội chính) / Tim Cadangan (đội dự bị),
  lưu đội hình và chiến thuật.
- **Shop** (Cửa hàng) đủ mọi tab (gói cầu thủ, vật phẩm tiêu hao, gói quà, MP Shop, gói tháng và gói
  nối tiếp, thưởng đặc biệt), mở gói với cấp thẻ ngẫu nhiên, Item saya (vật phẩm của tôi), Bag (hộp quà),
  quà đăng nhập hằng ngày. **Gudang** (Item saya > Gudang, kho): chuyển gói và vật phẩm tiêu hao vào và
  lấy ra. Gói thêm: **All Season +8** (mùa bất kỳ, +1…+8) và một gói cho '07 cùng mỗi mùa có trên
  fifaaddict.
- Luôn có VIP + ưu đãi phòng máy chính thức (IGR), xóa / khôi phục HLV, thẻ mùa '17.
- Danh sách sơ đồ (sơ đồ riêng của CLB + 33 sơ đồ phổ biến của game, đọc từ bảng sơ đồ của client) và
  cấp HLV / cầu thủ: EXP sau mỗi trận, cầu thủ LV.1-23 (+1 mọi chỉ số mỗi cấp), HLV LV.1-100.
- Issue #44: *Lepas* (giải phóng) trong danh sách cầu thủ xóa thẻ vĩnh viễn (trước đây cầu thủ vẫn còn
  trong danh sách; không áp dụng cho cầu thủ đá chính hoặc khi chỉ còn 18 cầu thủ); cầu thủ dự bị được
  tung vào sân cũng nhận EXP (dự bị không ra sân thì không); ô phần thưởng của cửa sổ Quest hiển thị ảnh
  đúng tỷ lệ (trước bị kéo giãn); team color "Red Day Boost" ("Red Devil" bản tiếng Thái) là của
  Manchester United (thẻ '17 / cầu thủ đang hoặc từng ở CLB), trước đây CLB nào cũng có; danh sách đội
  hình hiển thị mũi tên phong độ của từng thẻ (tốt nhất đến tệ nhất, đổi mới mỗi ngày lúc 00:00 WIB; chưa
  kiểm tra phong độ ảnh hưởng gì trong trận). *Kontrak pemain* (craft của FO3) đã chạy: 30 cầu thủ Legend
  tốt nhất, mỗi người cần 10 mảnh cầu thủ, vật phẩm "Kontrak pemain" (Shop) và 5% giá thẻ bằng EP; "Kotak player
  piece" (Shop, thẻ thưởng sau trận mặc định; trang admin có thể đặt vào bất kỳ phần thưởng nào) cho 1-3 mảnh của một cầu thủ.
  Tab Shop của trang admin đổi được danh sách (thẻ bất kỳ), số mảnh, phí và cấp.
- Âm thanh game và nhạc menu EA Trax. `tools/eatrax_song.py` đặt bài hát của bạn lên đầu, nhưng sau đó
  game bị crash sau mỗi trận (xem lỗi đã biết bên dưới).
- Trang quản trị web tại http://127.0.0.1:8088/ (EP, cầu thủ, gói, quà, quà hằng ngày, thưởng sau trận, cài đặt trận, kinh tế, chat AI, Cửa hàng, nhật ký)
  có chọn ngôn ngữ (issue #15): Bahasa Indonesia, English, 简体中文, ไทย, Tiếng Việt, 한국어; trình duyệt
  nhớ lựa chọn.
- Áo đấu CLB thật trong trận (kit id = `teamid << 5 | type`; kiểu cũ `<< 10` hiện thành ô bàn cờ).
- Trận đấu chơi hết hiệp hai khi bật **team color** (thưởng đội hình) (trước đây thưởng theo mùa làm crash
  lúc nghỉ giữa hiệp), rồi hiện màn hình EXP và phần thưởng.

Đã thêm và qua kiểm thử, nhưng chưa kiểm tra trong client:

- Bản thân phần thưởng **team color**: 11 cầu thủ đá chính được cộng OVR khi cùng CLB (4-5 người +1, tối
  đa 10-11 người +4), cùng quốc gia hoặc cùng mùa (5-6 người +1, tối đa 9-11 người +3); mỗi cầu thủ chỉ
  tính mức tốt nhất.
- **Opsi > Kontrol** (phím bàn phím và tay cầm) được lưu: máy chủ luôn gửi bố cục phím mặc định của game,
  nên màn hình đó áp dụng và lưu được thay đổi (trước đây thay đổi bị mất).
- **Chọn áo đấu** ở màn hình cài đặt trận (sân nhà / sân khách mỗi bên) được dùng trong trận.
- **Màn hình chọn HLV** hiện cầu thủ đắt giá nhất của CLB trong áo của CLB (trước đây hiện cầu thủ
  Barcelona mặc định của game).
- Sửa crash: popup thông báo lúc khởi động (URL web trống), hướng dẫn giải đấu Beginner's Guide (thiếu
  script).
- **Tỉ lệ nâng cấp**: tỉ lệ cơ bản theo cấp đích, nhân với nhãn "Rasio bonus keberhasilan" mà tab Upgrade
  hiện theo cấp của thẻ nguyên liệu (Kosong x1, Low x1.1, Normal x1.25, High x1.5). Nâng cấp thất bại từ
  +5 trở lên bị tụt một cấp.
- **Gói Top 200**: các gói thêm giờ cho 200 thẻ tốt nhất của mùa đó ("17 Top 200", "07 Top 200", …);
  **All Season Top 300 +8** cho thẻ mùa bất kỳ có chỉ số từ 81 trở lên.
- **Nâng cấp bằng thẻ +0** (issue #5): thẻ +0 của chính CLB giữ cấp thẻ đích ở 100% (+4 dùng +0 vẫn là +4)
  và thẻ +10 cũng dùng được; thẻ được giữ lại rời Tim Cadangan khi nó thế chỗ trong đội hình của thẻ bị dùng.
- **Upgrade 2.5** (màn hình Upgrade mới hơn của client, `features...enhancement.v2_5`):
  - **điểm nâng cấp**: nâng cấp thất bại cho điểm; "Gunakan poin" dùng điểm để được +10%; mua thêm miễn phí
    ở Shop > Paket (Upgrade Point 100 / 500 / 1.000);
  - **vật phẩm tăng tỉ lệ** Enhancement Boost Gold / Silver / Bronze (+15 / +10 / +5%), miễn phí ở
    Shop > Dapat dikonsumsi;
  - **sự kiện nâng cấp** vĩnh viễn (+10% cho cấp đích +1…+9, `GET /events`);
  - "Coba pengembalian lagi" lấy lại cấp bị mất do thất bại, bằng điểm.
- **Tukar v2** (`trade2`): giao dịch được đánh giá theo giá trị (TP) qua 15 mức, mỗi mức có cấp đề nghị tối
  đa; tab hiện HLV khác "hôm qua" nhận được gì ở mức đó; thẻ mùa huyền thoại tính 1.5 lần; không giới hạn
  theo ngày hay giá trị. Tukar Scout vẫn dùng song song.
- **Tukar Scout**: hai tuyển trạch viên khác của client, Scout Khusus (sự kiện; nhiệm vụ mùa huyền thoại) và
  VVIP Scout (đề nghị tốt nhất; nhiệm vụ 1 tỉ EP). Bỏ Scout Amatir để danh sách vừa 5 dòng (trước đây khi
  danh sách cuộn được, nhiệm vụ hoàn thành lại sáng thẻ của tuyển trạch viên khác hoặc không sáng gì).
- **Thưởng đội hình theo cấp nâng** ("Upgraded Team" trong client): cầu thủ đá chính cấp +2, +5 hoặc +8 trở
  lên; 5-6 / 7-8 / 9-11 người cho +1 / +2 / +3, +2 / +3 / +4 và +3 / +4 / +5 OVR. Loại một cấp ("+3 club" …
  "+10 Golden summit") cần cả 11 người đá chính ở cấp đó hoặc cao hơn: +4 (club +3/+4), +5 (silver +5…+7),
  +6 (Golden passion +8/+9), +7 (Golden summit +10). Cầu thủ nhận thưởng cấp nâng tốt nhất cộng thêm vào
  thưởng CLB / quốc gia / mùa tốt nhất.
- **Mọi team color có tên trong client** (hiện có 188):
  - 27 CLB (Real Madrid … Jeonbuk): các mức của Single team cộng thưởng riêng của CLB lấy từ tooltip trong
    game, vd. Barcelona MF agility +3;
  - 30 quốc gia: các mức của Satu Negara cộng vd. Spanyol MF ball control +2;
  - thưởng theo mùa với tên trong game (World Best tối đa +5, World Legend, 2014 WC All-Star, …);
  - khoảng 75 loại đặc biệt: '14 WC và EC từng quốc gia, nation BEST, MSN / BBC Trio, Arsenal
    Invincibles, Galácticos, LEGENDS, TOP 100, Power Header, New Star, ….

  Chỉ số thưởng và danh sách cầu thủ gốc là dữ liệu máy chủ và đã mất. Danh sách cầu thủ lấy từ cơ sở dữ
  liệu cầu thủ (mùa, CLB, cầu thủ được nêu tên, giới hạn chỉ số); chỉ số thưởng lấy từ dòng tooltip duy nhất
  game còn giữ. Không đưa vào: loại [Test], "+2~7", "14 WC Single Nation" và bốn mục CLB trùng lặp.
- **Formasi** (sơ đồ) không còn mở ra với "Tidak cukup pemain" (không đủ cầu thủ) (màn hình này tải đội hình
  mẫu 1, thứ máy chủ chưa từng gửi). Chỗ trống trong đội hình chính (issue #7, nâng cấp hai thẻ đá chính
  của cùng một cầu thủ) được lấp từ ghế dự bị, và ghế dự bị không bao giờ có thẻ thứ hai của cùng một cầu
  thủ. Mỗi cầu thủ đá chính nhận dữ liệu `starting` (workrate và vị trí trên sân): thiếu nó game hiện cầu
  thủ đó ở ghế dự bị.
- **Tim > Peran** lưu đội trưởng và từng người đá phạt (issue #8: thiếu `PUT /game/my/team/captain` và
  `/game/my/team/kickers/<0-4>`). Năm vị trí đá phạt (phạt góc trái, phạt góc phải, đá phạt, phạt đền,
  đá phạt 2) giữ nguyên chỗ; vị trí trống lấy một cầu thủ đá chính.
- **Tim > Locker room** (phòng thay đồ) chạy được (issue #13): kiểu áo và tay áo, hình xăm, băng cổ tay,
  găng tay (thủ môn: 39 mẫu găng), tất, bó gối, 614 đôi giày theo hãng, và số áo (chọn số đã có người mặc
  sẽ đổi với người đó). Trước đây thiếu bảng vật phẩm `/static/db/LOCKERROOMITEM`, nên mọi danh sách đều
  trống và bảng giày bị lỗi. Diện mạo cầu thủ được lưu trên thẻ, và Item saya có một vé phòng thay đồ vĩnh
  viễn, thứ game cần để hiện diện mạo đó trong trận. Băng cổ tay, găng tay và bó gối chỉ có mẫu sẵn của
  client (mỗi loại một).
- **Quản lý Cửa hàng trên trang quản trị** (issue #10): mọi tab Cửa hàng (gói cầu thủ, vật phẩm tiêu hao,
  gói quà, gói tháng, Đề xuất, MP Shop, Thưởng đặc biệt, gói nối tiếp). Tạo gói mới (theo mùa, top N, OVR,
  cấp tối đa, vị trí, giải đấu), hộp ngẫu nhiên, thẻ EP, gói quà và gói tháng, với bất kỳ biểu tượng nào
  trong 147 biểu tượng cửa hàng của client. Sản phẩm có sẵn có thể sửa, ẩn hoặc đặt giá cố định, và cả
  Cửa hàng có thể miễn phí hoặc không. Thay đổi lưu vào `gamedata/shop.json`.
- Phiếu **Simulasi Liga** (mô phỏng giải đấu) (vật phẩm 2001, Shop > Dapat dikonsumsi): nút Simulasi của
  Liga cho kết quả trận mà không cần chơi, mỗi trận một phiếu. Màn hình mô phỏng phát lại bàn thắng, cú sút,
  pha cứu thua và thẻ phạt khớp với kết quả (trước đây `eventlist` trống nên hiện 0-0), và màn hình kết quả
  sau đó hiện tỉ số, điểm và thống kê cầu thủ, tỉ lệ kiểm soát bóng và MOTM (trước đây trống), rồi phần
  thưởng EP và quay lại menu Liga. Nút này đếm số phiếu trong Item saya và từng bị tắt vì chưa có vật phẩm.
  Có thể tạo phiếu mới trên trang quản trị (loại "Phiếu Mô phỏng giải đấu").
- **Màn hình đội hình trước trận** (Liga và Cup, issue #16): đổi chỗ, thay người và đổi sơ đồ giờ được
  giữ và lưu vào CLB (`PUT /match/<id>/side/<side>/squad` chưa có xử lý, nên mỗi thay đổi tự hoàn tác).
  Danh sách sơ đồ đã có dữ liệu, và thanh thể lực hiện đầy (không giữ độ mệt giữa các trận).
- **Chỉ số FW / MF / DF của đội khớp với cầu thủ** (issue #18): ở màn hình đội hình và sảnh, đó là trung bình
  chỉ số mà danh sách cầu thủ hiển thị (chỉ số ở vị trí của cầu thủ cộng cấp nâng cấp, level và team color).
  Trước đây tính từ chỉ số thẻ gốc, thấp hơn khoảng 12.
  Trước trận (Giải đấu, Cúp, Rank) danh sách cầu thủ không tính team color, nên các thanh chỉ số ở đó cũng
  không tính; phần thưởng vẫn có hiệu lực trong trận.
- **Các tab Rekor liga** (issue #19): bảng xếp hạng đã hiện (tab bị lỗi vì thiếu bảng `league2difficulty`, bảng này
  giờ cũng quyết định đội lên hạng hay xuống hạng cuối mùa). Rekorku liệt kê số trận, điểm, bàn thắng, kiến tạo,
  đường chuyền, tắc bóng và cứu thua của từng cầu thủ trong các trận đã đá của mùa, còn Rekor gabungan hiện đội hình
  tiêu biểu và những người dẫn đầu thống kê của mùa. Chỉ tính cầu thủ của mình, và trận mô phỏng không cộng thống kê cầu thủ.
- **Squad A / B / C trong Formasi** (issue #20): ô bên trái nút đội hình chọn một trong ba đội hình đã lưu
  (trước đây trống vì máy chủ không gửi số đội hình). Mỗi đội hình giữ đội chính, sơ đồ và vai trò riêng.
- **Cài đặt tay cầm và camera được giữ trong trận** (issue #21): sau Opsi > Konfirmasi, game đọc lại tùy chọn
  đã lưu từ phản hồi của máy chủ, trước đây không có, nên mỗi trận lại quay về tùy chọn lúc đăng nhập (bàn phím,
  camera cũ). Trong trận, F3-F9 đổi camera (F5 = Co-op).
- **Ganti tim (đổi đội) hiện câu lạc bộ và cầu thủ của bạn** (issue #23): màn hình câu lạc bộ của vật phẩm này
  giờ hiện giải đấu, đối thủ derby, sân nhà, năm xây và sức chứa, cùng cầu thủ của bạn trong áo đấu của câu lạc bộ
  mới với ngày sinh, chiều cao và cân nặng. Trước đây thông tin câu lạc bộ từ máy chủ không có những thứ này, và
  không có áo đấu thì cầu thủ 3D không bao giờ được vẽ.
- **Chỉnh được độ cao và zoom camera** (issue #24): trong Opsi > Kamera setting, các thanh Customise (độ cao
  và zoom; với camera Pro là zoom, tốc độ và độ lắc) trước đây không có giá trị và không chỉnh được,
  vì máy chủ gửi thiết lập camera rỗng. Giờ chúng bắt đầu ở 10 (trên 20).
- **Có đủ đội tuyển quốc gia trong Tim lain** (issue #24): giải International (quốc gia cuối cùng
  trong danh sách) có 40 đội tuyển, trong đó có Ý, Anh, Đức, Tây Ban Nha và Pháp. Đội hình đội tuyển
  lấy từ liên kết đội tuyển của từng cầu thủ; trước đây chỉ có các đội mà cầu thủ không thuộc câu lạc
  bộ có bản quyền.
- **Vị trí cầu thủ trên màn hình kết quả** (issue #26): sau trận, sân trên màn hình kết quả xếp chồng
  mọi cầu thủ ở khung thành vì game báo mọi cầu thủ là thủ môn. Giờ máy chủ gán cho mỗi cầu thủ vị
  trí theo ô của họ trong đội hình.
- **Thẻ thưởng sau trận** (issue #26): các thẻ trên màn hình thưởng trước đây trống. Giờ có ba thẻ:
  thẻ bạn chọn cho EP (1.000 đến 10.000), gói cầu thủ (Semua Pemain hoặc '14 Top 200) hoặc vật phẩm
  Lucky EP, hai thẻ còn lại cho thấy phần thưởng có thể nhận.
- **Gói draft và viên EXP** (issue #27): Shop > Pak pemain bán gói draft ("06 Draft Pick 3",
  "2017 Draft Pick 5", "World Legend Draft Pick 3"). "Gunakan" mở màn hình chọn cầu thủ, điền sẵn các thẻ
  giá trị nhất của mùa đó; chọn N thẻ của mùa đó (tìm theo tên, giải đấu hoặc câu lạc bộ) rồi một thẻ trong
  số đó được mở. Shop > Dapat dikonsumsi bán viên EXP (1.000, 5.000, Max = LV.23, một viên cho LV.10 trở
  xuống, một viên cho thẻ 2017): chọn một cầu thủ và anh ấy nhận EXP đó. Trước đây thiếu các yêu cầu của
  màn hình draft (`/item/draftpack/<id>/players/<n>`, `/transfer/price/default/<grade>`) và loại vật phẩm
  viên EXP. Trang admin tạo thêm được cả hai (ô "Draft" của gói, loại vật phẩm "EXP Capsule").
- **Multiple > Rank: các HLV khác và trận đấu thật** (issue #30): màn hình xếp hạng trước đây dừng ở lỗi UI
  vì máy chủ không gửi hạng đấu, phần thưởng và thành tích ladder. Giờ màn hình mở được, và các HLV ảo lấp
  đầy bảng xếp hạng: top 3 (VS Manager Top) và xếp hạng bạn bè gồm 20 người, mỗi người có tên, câu lạc bộ,
  hạng đấu và thành tích thay đổi dần trong ngày khi họ thi đấu. Lúc đầu có 100 HLV, thêm 1.850 người khác, rồi mỗi
  ngày thêm 0-3 người mới, tối đa 2.000 (tăng gấp đôi theo issue #36). Mỗi người đá theo lối chơi riêng (kiểm soát bóng, phản công, pressing tầm cao, bóng dài,
  dựng xe buýt hoặc tấn công tổng lực: sơ đồ, chiến thuật và cách chạy chỗ) trong bộ áo của câu lạc bộ mình,
  với thẻ ngẫu nhiên từ mọi câu lạc bộ và mùa giải quanh rating của họ, mỗi thẻ có cấp thẻ riêng (từ +1 với
  người mới đến +6 trở lên) và level riêng. Ngày qua ngày, họ mua bán thẻ, nâng cấp thẻ (nâng cấp thất bại
  từ +5 bị tụt một cấp) và tăng level cho thẻ. Rank 1 on 1 và VS Manager đá một trận thật với một trong năm
  HLV có rating gần bạn nhất: màn hình đội hình, cài đặt trận, rồi cả trận với AI của game điều khiển phía
  đối thủ, sau đó là kết quả, phần thưởng và rating xếp hạng mới của bạn, hiện trong các bảng của màn hình
  Rank, trong cửa sổ thành tích (các trận gần nhất, biểu đồ mùa giải) và trong danh sách trận đấu của
  Info Manager. Trong trận, đối thủ chat trong khung chat của trận theo tính cách riêng (lúc giao bóng, khi
  có bàn thắng của hai bên, sút trượt, cứu thua, và trả lời khi bạn gõ chat), rồi bình luận về kết quả khi
  quay lại màn hình Rank; tab Chat AI trên trang quản trị dùng để tắt hoặc chọn ngôn ngữ (Indonesia, Anh,
  Việt). Messenger giờ mở được (trước đây nó chờ một danh sách thông báo mà máy chủ không bao giờ gửi) và
  hiện 20 người bạn ở trạng thái online, bận (đang trong trận Rank) hoặc offline theo khung giờ chơi của họ
  (08:00-02:00 WIB); nút Info của một người bạn hiện hồ sơ và đội hình của người đó.
- **Market sôi động** (issue #30): các HLV ảo mua thẻ bạn đăng bán ("Dijual" tự chuyển thành "Terjual" trong
  vài phút, nhanh hơn khi giá rẻ hơn, kèm thông báo nổi "terjual seharga" và một thông báo ghi tên người
  mua; Batal và Ulang dùng được) và tự đăng bán thẻ của mình bên cạnh lượng thẻ +1..+3 có sẵn, cả ở các cấp
  thẻ khác, với giá thấp hơn giá thị trường một chút, còn Kuantitas thay đổi mỗi giờ. Tab Market Price trong
  cửa sổ thẻ vẽ biểu đồ giá của 30 ngày gần nhất.
- **Cài đặt kinh tế** (issue #30): tab Ekonomi (Kinh tế) mới trên trang quản trị cho bạn chỉnh độ khó của
  game tùy ý: hệ số nhân giá cầu thủ và giá gói, EP ban đầu của HLV mới, tùy chọn Shop miễn phí và Market
  biến động. Với Market biến động, giá mỗi cầu thủ lên xuống theo từng ngày (tăng hoặc giảm tối đa khoảng
  13%), thỉnh thoảng HLV ảo đăng bán thẻ giá hời, và bạn có thể đặt giá bán đến 105% giá thị trường (nhưng
  lâu có người mua hơn), nên mua rẻ bán đắt sẽ có lãi. Các mẫu có sẵn: Dễ (như trước: Shop miễn phí, giá cố
  định), Thực tế (Shop tính phí theo giá trị các thẻ trong gói, Market biến động, 300.000 EP ban đầu) và Khó
  (giá x2, 100.000 EP). EP nhận sau mỗi trận vẫn được chỉnh ở tab Thưởng sau trận.
  Khi Shop tính phí, phiếu Mô phỏng giải đấu có giá bằng phần thưởng của một vòng, và với Market biến động,
  giá gói bằng giá trị các thẻ lúc đắt nhất (+13%), vì gói có thể để dành mở vào bất kỳ ngày nào. Luật chặt
  (bật ở Khó) khiến Nâng cấp, Tukar và Scout không tạo EP: phí Nâng cấp bằng giá trị trung bình nó thêm vào
  (khôi phục trả bằng EP), đề nghị của Tukar và Scout không vượt quá giá trị các thẻ đã đưa. Khi tắt (Dễ,
  Thực tế) dùng luật gốc của game: phí Nâng cấp 2% giá trị thẻ, khôi phục bằng điểm nâng cấp, đề nghị không
  giới hạn.
  Vật phẩm không chứa EP (tăng tỉ lệ nâng cấp, điểm nâng cấp, viên EXP, đổi tên hoặc đổi đội) có giá cơ bản
  nhân hệ số giá cầu thủ, nên ở mức Khó giá gấp đôi.
  Thay đổi có hiệu lực ngay cả khi game đang chạy: máy chủ báo game bỏ danh sách Shop nó giữ suốt phiên, nên
  tab Shop hiện giá mới ở lần mở tiếp theo; tab nào đã mở kể từ lúc khởi động game thì vẫn giữ giá cũ cho đến
  khi khởi động lại game. Việc kiểm tra hàng giới hạn của Shop không còn báo "Kesalahan UI telah terjadi".
- **AI Chat nhiều gấp mười lần** (issue #30): HLV ảo có 20 câu cho mỗi tình huống và mỗi tính cách ở mỗi ngôn
  ngữ, tổng cộng 2.700 câu: chào hỏi và chuyện bóng đá lúc giao bóng, phản ứng khi ghi bàn, sút hỏng và cứu
  thua, trêu đùa khi bạn gõ chat, và nhận xét về kết quả. Không câu nào chứa từ mà bộ lọc chat của game sẽ che
  (bộ lọc còn che cả từ nằm bên trong từ dài hơn).
- **HLV ảo xây đội hình theo cách riêng** (issue #30): mỗi người có một chủ đề đội hình cố định: thẻ bất kỳ
  gần rating của họ; một CLB hàng đầu (Real Madrid, Barcelona, Atletico, hai đội Manchester, Chelsea,
  Liverpool, Arsenal, Tottenham, Bayern, Dortmund, Juventus, Milan, Inter, Roma, Napoli, PSG) với cầu thủ
  hiện tại hoặc từng khoác áo; một đội tuyển hàng đầu (Brazil, Argentina, Tây Ban Nha, Đức, Pháp, Anh, Ý,
  Hà Lan, Bồ Đào Nha, Bỉ, Uruguay, Croatia); một team color của game (một mùa thẻ hoặc một bộ có tên); một
  ngôi sao cùng đồng đội CLB hoặc đồng hương; meta; hoặc một chỉ số (tốc độ, thể lực, kỹ thuật, chuyền,
  phòng ngự, dứt điểm, cầu thủ trẻ, cầu thủ thế hệ cũ). HLV càng mạnh càng hay theo meta và team color.
  Chủ đề chỉ lấy thẻ gần rating của họ nên đội hình không mạnh hơn vì nó.
- **Cài đặt trước trận** (issue #31): các nút chọn trên màn hình cài đặt trận của Multiple > Rank đã dùng
  được cho sân (70 sân có đủ dữ liệu trong client), thời gian trong ngày (ban ngày hoặc ban đêm), thời
  tiết (quang đãng, mưa, tuyết, âm u: thành hoàng hôn nếu là ban đêm), thời lượng trận (6, 8, 10 hoặc 12
  phút) và quả bóng (9 loại bóng). Lựa chọn gần nhất được giữ cho các trận tiếp theo, kể cả Liga và Cup;
  màn hình trước trận của hai chế độ này chỉ hiện các giá trị, nên tab Cài đặt trận mới trên trang quản
  trị cũng chỉnh được chúng, và còn chọn được "sân của đội chủ nhà" hoặc "ngẫu nhiên". Máy chủ sửa hai
  nhãn trong chuỗi tiếng Indonesia của client: ban ngày từng ghi là "Waktu" (thời gian), âm u là "Malam"
  (đêm); màn hình của Liga/Cup vẫn gọi âm u là "Salju" (tuyết).
- **Bàn phím và tay cầm giữ preset điều khiển riêng** (issue #32): Opsi > Kontrol chỉ lưu một preset, cho
  thiết bị đang dùng, và đưa thiết bị còn lại về preset đầu tiên khi bạn chuyển thiết bị. Giờ đây máy chủ
  nhớ preset dùng gần nhất của từng thiết bị và khôi phục nó sau khi bấm Konfirmasi (màn hình hiện preset
  đó ở lần mở tiếp theo; trận đấu dùng nó ngay từ lúc giao bóng).
  Để màn hình hiện ngay preset của thiết bị khi chuyển, hãy đóng game rồi chạy một lần
  `python tools/control_presets.py`: công cụ này vá màn hình Kontrol của client (có lưu bản sao lưu;
  `--remove` khôi phục bản gốc).
- **Menu Manager đã có dữ liệu** (issue #33): Info Manager hiện thành tích của bạn thay vì "-": số trận, bàn
  thắng, số trận thắng và chuỗi thắng ở Giải đấu, Cúp và Rank, các chức vô địch giải và mức độ đã đạt, số cúp đã
  thắng cùng tiền thưởng, số thẻ đã mua và bán cùng thẻ đắt nhất của mỗi loại, thời gian chơi, phần giới thiệu và
  ảnh của bạn. Các số liệu được tính từ phiên bản này. Manager Ranking hiện giá trị đội của bạn trong 30 ngày qua
  so với mức trung bình của 20 người bạn, thứ hạng của bạn trong nhóm đó và thay đổi thứ hạng so với hôm qua.
- **Nhiệm vụ và thành tích** (issue #33): Manager > Daftar pencapaian có các dòng huy hiệu (cấp HLV, số trận,
  số trận thắng, nâng cấp, cấp thẻ cao nhất, giá trị đội, bàn thắng, thắng Rank), mỗi thành tích có 5 cấp thưởng
  EP, gói và vật phẩm để nhận, và đeo được huy hiệu khi xong cả dòng; thêm một dòng nhiệm vụ hằng ngày (chơi,
  thắng, ghi bàn, chơi Rank) làm mới lúc 00:00 WIB. Nút Quest trên thanh trên cùng mở nhiệm vụ hằng tuần (làm
  mới thứ Hai lúc 00:00 WIB) kèm đồng hồ đếm ngược; nhiệm vụ hoàn thành thưởng GP để đổi các hộp
  quà trong cửa sổ đó (EP, Lucky EP, gói, tăng tỉ lệ nâng cấp, Kotak Emas). Máy chủ đếm các trận bạn chơi và các
  lần nâng cấp thành công. Tab Misi mới trên trang quản trị dùng để bật hoặc tắt, chọn ngôn ngữ (tiếng Indonesia,
  tiếng Anh, tiếng Việt) và hệ số mục tiêu, phần thưởng; thưởng EP cũng theo giá cầu thủ ở tab Kinh tế.
- **Trọng tài lúc giao bóng** (issue #33): hai trợ lý trọng tài được tạo từ một bản ghi trống (không có trọng tài
  đó trong danh sách của game), với đầu, tóc và thân không có tệp tài nguyên, nên trông méo mó khi xếp hàng ra sân.
  Giờ máy chủ dùng các trọng tài có thật trong game.
- **Bình luận tiếng Việt từ client FO3 VN** (issue #34): khi đã tắt game, chạy
  `python tools/commentary.py "<thư mục client FO3 VN>"`. Script chép `audiodata\speechdata\vie_vn` của client
  VN vào cạnh phần bình luận tiếng Anh của client này (`eng_us`) và chỉ để tiếng Việt làm ngôn ngữ bình luận
  trong `data\loc\locale.big` (có lưu bản sao lưu); sau đó game đọc bình luận từ `vie_vn`. `--remove` trả lại
  bình luận tiếng Anh.
- **Thưởng lên cấp HLV** (issue #35): mỗi cấp HLV từ 2 đến 100 nhận phần thưởng lên cấp của FO3 (bảng trên
  fifaaddict): EP cùng vật phẩm như gói Top 300, Top 100 và Best, gói giải đấu 65+ và GK 65+, phiếu mô phỏng giải
  đấu, phiếu đổi đội và EXP capsule. Phần thưởng vào Bag ("Hadiah naik level Lv.N"); màn hình sau trận hiện chúng
  trong cửa sổ lên cấp, và biểu tượng thông tin liệt kê phần thưởng các cấp tiếp theo. Vật phẩm FO3 mà máy chủ
  không dùng (gói staff, Red Ball, phiếu phòng thay đồ và tăng thưởng, chuyển EXP) bị bỏ qua. Thưởng EP theo giá cầu
  thủ ở tab Kinh tế.
- **Câu lạc bộ** (issue #36): Klub đã dùng được. Lập CLB của riêng bạn (10.000 EP; "Konfirmasi" sau
  "Klub telah dibuat" không còn bị xám mãi) hoặc vào một trong 60 CLB AI do các HLV ảo điều hành, mỗi CLB có chủ
  CLB, thành viên, cấp, phần giới thiệu và tin tức. Các HLV chưa có CLB sẽ xin vào CLB của bạn
  (Manajemen > permohonan, hoặc vào ngay khi CLB đang nhận thành viên không cần duyệt). Thành viên online theo
  giờ chơi của họ, tích điểm hoạt động, đăng bài lên bảng tin và trò chuyện ở tab Klub của cửa sổ chat: họ trả
  lời lời chào và tin nhắn của bạn, chào đón thành viên mới, chúc mừng hoặc động viên theo kết quả trận của bạn,
  bằng ngôn ngữ chat chọn trên trang quản trị (tiếng Indonesia, tiếng Anh, tiếng Việt). Điểm CLB trong tuần mang
  lại phần tăng thưởng cho trận đấu (EXP HLV, EXP cầu thủ, EP), và khi chính bạn đạt 30 điểm trong một ngày thì
  nhận 1.000 EP vào Bag. Chưa làm: trận đấu CLB, cơ sở vật chất CLB và bảng xếp hạng CLB.
- **Giao hữu với AI** (issue #36): Multiple > Persahabatan hiện lại các nút. Quick match và Random match tìm một
  HLV ảo làm đối thủ; phòng tự tạo đá vs COM (CLB và cấp độ chọn trong bộ chọn AI, từ Amateur đến Legendary) hoặc
  với một người bạn mà bạn mời từ danh sách. Kết quả được tính là trận giao hữu trong Info Manager, không tính
  vào rating xếp hạng, và đối thủ chat như ở Rank.
- **Hiệp phụ và đá luân lưu ở Rank và King of the Hill**: hòa sau 90 phút (Rank 1 vs 1, Rank Manager, King of
  the Hill) thì đá hiệp phụ rồi luân lưu; thắng luân lưu được tính là thắng cho rating, thành tích và phần
  thưởng trận.
- **King of the Hill** (issue #36): giải khu vực Esports có một mùa giải mỗi tháng. Chọn một trong 19 vùng của
  Indonesia; bảng xếp hạng của vùng đó gồm các HLV ảo trong vùng, và Mulai cho bạn đá với một người trong số họ
  có rating gần với rating King of the Hill của bạn. Màn hình phần thưởng hiện số điểm được hoặc mất.
  Tab Cài đặt trận đấu trên trang quản trị đổi khu vực sang các thành phố của Indonesia, Singapore /
  Malaysia, Thái Lan hoặc Việt Nam (tên tiếng Việt), hoặc khu vực tùy chỉnh viết ở đó, mỗi dòng
  "Vùng: thành phố, thành phố" (ví dụ thêm Sa Pa vào Miền Bắc).
- **Ảnh thông báo lúc đăng nhập** (issue #36): tab Pengumuman (Thông báo) trên trang quản trị tải lên ảnh JPG
  hoặc PNG (chỉnh theo popup 800×720, có xem trước) cho popup Thông báo lúc đăng nhập, hoặc xóa ảnh đó.
- **Lịch sử CLB đến mùa này** (PR #39 của fifamvp): tab Karakteristik trong thông tin cầu thủ hiện sự nghiệp CLB
  của thẻ (`features.seasoncare`). Lịch sử gốc của FO3 dừng khoảng 2017 và chỉ hiện ở thẻ mùa 22-24; giờ hiện ở mọi
  thẻ của cầu thủ có lịch sử, và tab Aset trên trang quản trị (Lịch sử CLB, hoặc `python tools/club_history.py fetch`)
  lấy sự nghiệp đến mùa hiện tại từ Wikidata (năm, CLB, cho mượn; CLB FO3 chưa có tên sẽ được thêm tên trong client).
  Sau đó bấm Cơ sở dữ liệu > Áp dụng vào game.
- **Tải ảnh còn thiếu từ trang quản trị** (issue #36): tab Aset (Hình ảnh) tải ảnh mặt cầu thủ (ảnh mùa của từng thẻ và ảnh
  của cầu thủ), logo CLB, logo
  giải đấu và ảnh staff còn thiếu từ fifaaddict.com ở chế độ nền (mỗi lần một loại; dừng và tải tiếp lúc nào cũng
  được). Tab này chạy `tools/fa_faces.py`, công cụ này giờ nhận thêm `--leagues` và `--uniforms` (ảnh áo đấu ở màn hình chọn CLB, issue #43). "Default ball" trên màn hình
  cài đặt trận hiện quả bóng EA SPORTS, cờ nhỏ dùng ảnh cờ lớn, và các màn hình Cup có cúp và skin riêng.
  Ảnh cầu thủ fifaaddict không có (hoặc từ chối) sẽ lấy từ FC Online (FO4) (cùng player id của EA; cầu thủ mới của
  server lấy ảnh thẻ FO4 từ fifaaddict), việc quét bao gồm mọi thẻ trong cơ sở dữ liệu, khi bị từ chối sẽ chờ lâu hơn rồi
  tiếp tục thay vì dừng, và *Tải tất cả ảnh còn thiếu* lấy mọi loại trong một lần.
- **Tên phần thưởng khi dùng gói ngôn ngữ** (issue #36): với gói tiếng Việt, danh sách thành tích hiện
  "undefined" trước mỗi phần thưởng và báo lỗi UI, vì máy chủ chỉ đặt tên vật phẩm thưởng bằng tiếng Indonesia và
  tiếng Hàn. Giờ mọi ngôn ngữ của client đều có tên.
- **Exhibition với AI** (issue #36): Multiple > Exhibition mở được phòng. Chọn CLB có bản quyền bằng mũi tên
  hoặc Pilih Team, mời bạn từ danh sách (HLV ảo, vào phòng với CLB tự chọn và sẵn sàng ngay), rồi Siap để
  đá với đội hình thật của hai CLB. Mỗi ngày 5 trận; thẻ của bạn không nhận EXP từ các trận này. Live Center
  mở được mà không còn hai lỗi UI.
- **Live Boost từ Team of the Week của EA FC**: Tim > Live Boost hiện Team of the Week
  tuần này (lấy từ fut.gg; máy chủ tải tuần mới sau khi công bố vào thứ Tư nên cần internet). Mọi thẻ của
  các cầu thủ đó được +5 mọi chỉ số trong tuần (OVR +5): đội hình, chợ chuyển nhượng và trận đấu. Chỉ hiện
  cầu thủ FO3 có (và cầu thủ mới của 26 LIVE), không có thẻ nữ. Có đủ 11 người (+1 trở lên) nhận 100.000 EP
  mỗi tuần một lần (Terima).
- **Trình sửa nhiệm vụ hằng tuần** (tab Misi trang quản trị): danh sách nhiệm vụ được bốc mỗi tuần (loại,
  mục tiêu, GP, tên riêng với {0} là mục tiêu), số nhiệm vụ mỗi tuần (tối đa 6) và các hộp quà GP của cửa
  sổ Quest (tối đa 5), kèm xem trước tuần này và tuần sau. Nhiệm vụ đã xong trong tuần vẫn được giữ.
- **Thưởng đăng nhập hằng ngày trong game**: thưởng của ngày vẫn vào Túi ở lần đăng nhập đầu; giờ có thông
  báo, và mục đó mở popup cho biết ngày thứ mấy cùng vật phẩm.
- **Thưởng thời gian chơi**: nhận thưởng khi thời gian chơi hôm nay đạt 10, 30 và 60 phút (ngày tính từ
  00:00 WIB; nghỉ quá 20 phút không tính), vào Túi kèm thông báo như trên. Mốc thưởng chỉnh ở tab Daily
  reward của trang quản trị.
- **Chat toàn cầu**: tab Channel của cửa sổ chat đã hoạt động: tin nhắn của bạn hiện lên, các HLV ảo đang
  online thỉnh thoảng trả lời và tự trò chuyện (theo ngôn ngữ của tab Chat AI).
- **Bàn chuyện bóng đá trong chat toàn cầu**: các HLV ảo ở tab Channel bàn về bóng đá trong ngày: các tìm
  kiếm Google đang tăng ở một khu vực liên quan đến bóng đá và tiêu đề Google News bằng ngôn ngữ đó (X,
  Instagram và TikTok cần đăng nhập, Reddit từ chối máy chủ), cập nhật mỗi ngày một lần. Mỗi HLV thích nhắc đến CLB,
  cầu thủ ngôi sao hoặc đất nước của mình khi có trong tin, rồi người khác trả lời. Tin toàn cầu luôn
  bật; Indonesia, Malaysia, Việt Nam, Thái Lan và Hàn Quốc chọn tay ở tab Chat AI của trang quản trị, nơi
  cũng hiện các chủ đề hiện tại.
- **Phần thưởng Rank trên trang quản trị**: tab Match sửa thưởng mùa của từng hạng (chính và phụ) và các mốc
  số trận thắng trong tuần, cũng hiện ở màn hình Rank. Giờ phần thưởng thật sự vào Túi: mốc tuần kèm popup của
  game ở màn hình Rank, thưởng mùa khi mùa mới bắt đầu (thưởng chính của chế độ có điểm cao nhất, thưởng phụ của
  mỗi chế độ khác đã chơi).
- **Phần thưởng King of the Hill trên trang quản trị**: tab Cài đặt trận đấu sửa thưởng tuần (mục tiêu số
  trận thắng hoặc số trận trong tuần ngày 1-7, 8-14, 15-21 hoặc 22-28, cộng thưởng hạng khi thắng đủ) và
  thưởng mùa (hạng cuối tháng khi thắng đủ, cộng thứ hạng trong khu vực). Màn hình King of the Hill hiện
  chúng; khi một tuần hoặc mùa đã chơi kết thúc, màn hình phần thưởng hiện ra lúc mở King of the Hill và
  phần thưởng đạt được vào Túi.
- **Giải đủ đội** (issue #40): một mùa Liga có mọi CLB đủ đội hình trong giải của mình (Premier League
  20 CLB, 38 vòng; tối đa 24) thay vì 10. EP và EXP theo thứ hạng điều chỉnh theo số đội.
- **CLB mà FO3 ID không có**: AC Milan, Genoa, Udinese, Bologna, Cagliari và Sampdoria trở lại game (bộ áo vẫn
  còn trong file game; `tools/fo4_season.py install` thêm CLB với màu đọc từ bộ áo), kèm đội hình 26 LIVE đầy đủ
  (`fetch` lấy danh sách, `collect` + `import` đọc chỉ số). Thẻ '17 của các cầu thủ này trước nằm ở đội tuyển
  quốc gia nay về lại CLB. Serie A trong Liga có 19 CLB, màn chọn CLB cũng hiện. Cách làm: fetch, collect +
  import, build, rồi install khi đã đóng game (hoặc Terapkan ke game trên trang quản trị).
  Thẻ FO3 đời cũ của cầu thủ CLB Ý trước nằm ở Rest of the World (vd. Pirlo '06 ở Milan; 322 thẻ) cũng về lại
  CLB; `restore` trả về như cũ.
- **Cơ sở dữ liệu thẻ và CLB trên trang quản trị** (issue #38): tab Database sửa từng thẻ cầu thủ theo mùa
  (spid = mùa × 1.000.000 + player id; tìm theo tên, spid hoặc player id): 33 chỉ số, vị trí, chân thuận và
  chân không thuận, kỹ năng, tỷ lệ công/thủ, đặc điểm, CLB (chuyển nhượng) và ngoại hình 3D (đầu của bất kỳ
  dòng 3D nào, ví dụ khuôn mặt của thẻ khác; kiểu và màu tóc, râu, da, mắt và vóc dáng trên đầu loại chung,
  màu tóc 0 = vàng; đầu quét giữ tóc riêng). Mùa tự tạo: mã, tên và huy hiệu, chứa bản sao của các thẻ khác
  (có thể +N mọi chỉ số), mỗi mùa có gói Top 200 và màu đội riêng. Thay đổi thẻ được ghi vào
  `gamedata/fifadb.sqlite` và `gamedata/card_edits.json` (giá trị gốc và mới: Kembalikan khôi phục thẻ, và
  `tools/fo4_season.py build` áp dụng lại), mùa vào `gamedata/seasons.json`; Ekspor / Impor mod chuyển tất cả
  sang server khác bằng một tệp JSON. Game đọc bản sao CSDL riêng: đóng game, bấm Terapkan ke game (chạy
  `tools/fo4_season.py install` ở chế độ nền), rồi chạy `play.bat`. Tab này cũng thay logo CLB trong bộ nhớ
  đệm của client (logo gốc giữ trong `gamedata/crest_backup`) và, trong hộp thoại thẻ, ảnh cầu thủ bằng PNG/JPG
  của bạn hoặc ảnh của spid / player id khác (từ bộ nhớ đệm hoặc fifaaddict.com) cho mọi thẻ của cầu thủ hoặc chỉ một thẻ (issue #36; `gamedata/faces/p<id>.png`, được phục vụ trước
  ảnh trong bộ nhớ đệm; ô Khuôn mặt 3D cũng tìm đầu theo tên cầu thủ). Chưa có áo đấu và CLB mới.
- **Khuôn mặt 3D từ ảnh** (tab Database, hộp thoại thẻ): với cầu thủ có khuôn mặt chung, ảnh chụp cận mặt từ
  phía trước trở thành da mặt trên đầu 3D đã quét của một cầu thủ khác (người cho: hình đầu và tóc đi kèm, mắt
  vẫn là của người cho). Bấm 11 điểm trên ảnh (mắt, mũi, khóe miệng, cằm, trán, thái dương, hàm), chọn người
  cho rồi bấm Tạo; Terapkan ke game đưa đầu này vào client (`data_7.big`, dựng lại từ bản gốc) cho mọi thẻ của
  cầu thủ dùng dòng player id hoặc đầu chung. Lưu trong `gamedata/photo_heads`. Ở khoảng cách camera trận đấu
  trông giống ảnh, với ánh sáng của ảnh và hình đầu của người cho.
  *Lấy từ ảnh cầu thủ* điền ảnh, các điểm và người cho từ ảnh FO3 của cầu thủ (trong bộ nhớ đệm, nếu không thì
  từ fifaaddict.com): bộ dò khuôn mặt YuNet (OpenCV Zoo, MIT; tải một lần vào `gamedata/yunet`) tìm mắt, mũi và
  miệng, người cho là đầu quét gần nhất về màu da và tóc (màu và kiểu, từ ảnh của chính người cho), không râu.
  Nút trong tab Database làm việc này cho mọi cầu thủ mặt chung trong CLB có ảnh (cầu thủ mới không có ảnh FO3
  bị bỏ qua).
  Màu da được so ảnh với ảnh (ảnh của chính người cho) và với texture mặt sẽ mang theo (ảnh sáng đọc ra sáng hơn
  texture: trước đây cầu thủ da sẫm nhận người cho da sáng); cầu thủ Đông Á/Đông Nam Á nhận người cho từ các
  nước đó, còn lại từ các nước khác (mắt của người cho được giữ); răng khi cười được thay bằng môi khép của người
  cho. *Tạo lại mọi mặt 3D từ ảnh đại diện* làm lại các mặt đã có.
- **Thế giới FO3 cổ điển** (issue #38): *Dunia game* trong tab Database chọn Hiện đại (CLB AI, phần chọn CLB
  và gói This Season dùng thẻ 26 LIVE, như trước; mùa tự tạo cũng hiện trong tìm kiếm Market) hoặc Cổ điển FO3:
  mọi CLB dùng đội hình 2017 gốc của FO3 (AC Milan, Genoa, Udinese, Bologna, Cagliari và Sampdoria, vốn FO3 ID không có, dùng thẻ FO3 của họ ở mọi năm; issue #43), This Season = '17, còn các mùa thêm vào (25 / 26 LIVE, WC18, mùa tự tạo)
  không có trong gói, Market, giao dịch hay đội hình quản lý ảo: chỉ trang quản trị mới trao được. CLB và bộ sưu
  tập của bạn giữ nguyên, thẻ gốc FO3 không bao giờ bị ghi đè (mùa mới được thêm bên cạnh). Lưu trong
  `gamedata/world.json`; có hiệu lực sau khi khởi động lại (`play.bat`).
- **Tạo lại quản lý ảo bất cứ lúc nào**: tab Chat AI trên trang quản trị có *Generate ulang*: một nhóm quản
  lý ảo mới (tên, cấp, CLB, đội hình, lối chơi, điểm Rank, khu vực, CLB AI) ngay lập tức, cùng ID nên bạn bè,
  CLB và lịch sử vẫn hoạt động; *Kembalikan ke awal* đưa nhóm đầu tiên trở lại. Chỉ nút này đổi nhóm: mỗi lần
  chạy vẫn như cũ (`gamedata/vm_gen.json`).
- **Màu đội tự tạo trên trang quản trị**: tab Team color tạo màu đội mới. Thẻ thành viên được chọn bằng bộ lọc
  (mùa, CLB hiện tại hoặc cũ, quốc gia, cầu thủ, OVR tối thiểu, N thẻ Market tốt nhất; thẻ phải thỏa mọi bộ lọc
  đã điền) và có một mức cộng (OVR hoặc một chỉ số, cho mọi vị trí hoặc FW / MF / DF / GK), các bậc giống màu đội
  đặc biệt có sẵn (5-6 / 7-8 / 9-11 cầu thủ trong đội hình chính). Lưu trong `gamedata/teamcolors.json`; hiện trong
  danh sách Team colour của game từ lần đăng nhập sau, không cần khởi động lại. Đội hình của quản lý ảo không dùng màu
  đội và mùa tự tạo, và tệp mod cũng mang theo chúng.
- **Staff** (Tim > Manajemen Staff): hệ thống staff của FO3 hoạt động trở lại. *Rekrut* (tuyển): gói staff
  bằng EP (Basic FW / MF / DF / GK / ALL, Pro, Premium, Giám đốc kỹ thuật, Premium Medical) và mỗi ngày một
  gói ALL miễn phí khi đăng nhập, thêm một gói khi chơi đủ 3 trận. *Staff yang dimiliki* (staff đang có): 7 ô
  vai trò (FW, MF, DF, HLV trưởng, GK, HLV thể lực, Giám đốc kỹ thuật); staff ở đúng ô tăng chỉ số cho các cầu
  thủ đá chính thuộc nhóm vị trí đó (HLV thể lực, HLV trưởng và Giám đốc kỹ thuật: mọi cầu thủ đá chính) trong
  danh sách đội hình và trong trận, với hợp đồng 50 trận gia hạn bằng EP. *Latihan* (huấn luyện): dùng staff
  khác để nâng cấp (0.5 đến 5 sao) và tăng chỉ số cho một staff. *Pembinaan* (tu nghiệp): gửi staff ra nước
  ngoài 5-10 trận để đổi hoặc tăng một chỉ số. Tên và chỉ số tăng theo danh sách fo3staff của fifaaddict; cấp,
  giá và hợp đồng do máy chủ tự đặt (số liệu của Garena đã mất), và giá EP theo giá cầu thủ ở tab Kinh tế.
  Client chỉ có ảnh của 7 staff; `python -I tools/fa_faces.py --staffs` thêm ảnh còn lại từ fifaaddict.
  Quản lý ảo thi đấu với team color của họ như bạn, và có staff theo cấp (thêm một ô vai trò mỗi 12
  cấp).
  Issue #41: *Rekrut* mua tối đa 10 staff một lần; tab Pak & Bag trên trang quản trị thêm bất kỳ staff nào
  (0.5-5 sao) vào tài khoản và xóa được; ảnh gói đặt ở `assets/externalAssets/shop/<icon>.png` (170×120: broall, brofw, bromf, brodf, brogk (Basic), silall (Pro), goldall (Premium), position (Technical Director), medical (Premium Medical), staffpack_2103 (free pack)),
  không có thì ô hiện ảnh staff; gói ngôn ngữ thiếu tên gói 11-13 (tiếng Thái) sẽ được bổ sung.
- **Phát triển cầu thủ** (Tim > Manajemen pemain > Perkembangan, tính năng FO3 bị tắt ở FO3 ID): thẻ cấp 23 dùng
  điểm chiến thuật để tăng chỉ số +1 mỗi bước, tối đa 60 điểm mỗi thẻ; giá mỗi bước theo giá trị chỉ số (4 dưới 60
  đến 60 từ 140, bảng phát triển FO3 của fifaaddict). Chỉ số tăng có hiệu lực ở mọi màn hình, xếp hạng đội và trận
  đấu. Điểm chiến thuật đến từ thông số các trận tự chơi (bàn thắng, kiến tạo, chuyền, tắc bóng, cứu thua... cộng kết
  quả; không tính mô phỏng), tối đa 1.200; màn hình sau trận hiển thị. Vật phẩm "Reset poin taktik" (Shop) trả lại
  điểm của một thẻ. Thẻ của HLV ảo cũng phát triển, HLV càng mạnh càng nhiều.
- **Mở được hộp ngẫu nhiên** (Kotak Platinum và các hộp khác, issue #12): màn hình hộp liệt kê phần thưởng
  từ `staticdata.availables`, trước đây bị thiếu, nên mọi hộp đều lỗi khi bấm "Gunakan" (dùng).
- **Thẻ mới vào Tim Cadangan**: thẻ từ gói, giao dịch, Market, Bag hoặc trang quản trị hiện "-" ở cột Tim
  trong Skuad, sẵn sàng cho Tukar Scout. Nút của thẻ chuyển nó sang Tim inti (RES màu xanh: Tim inti nằm
  ngoài 18 cầu thủ của đội hình), nơi chứa được cả CLB (200). Trang quản trị có thể chuyển mọi thẻ Tim
  Cadangan sang Tim inti cùng lúc.
- **Tìm kiếm trên Market** thấy thẻ của mọi mùa: game chỉ tìm các mùa trong `/static/roster`, vốn thiếu 29
  mùa có thẻ trên Market (World Legend, World Best, '14 TOTS, …), nên vd. WL R. Gullit không bao giờ hiện
  ra. Danh sách mùa trong bộ lọc Market đã dùng được (issue #14: chọn mùa báo lỗi "cannot convert 39 to
  Array", vì `value` của mỗi mùa phải là danh sách) và hiện tên mùa của client (World Legend, 02 Chinese
  Legend, …). Kết quả tìm kiếm không còn hiện các thẻ engine mà các CLB AI ở Liga và Cup dùng để thi đấu
  (mang biểu tượng mùa '17, chỉ số tới 127, vd. L. Messi 125; vốn cũng không mua được): mùa 0 của các thẻ
  này đã được bỏ khỏi `/static/roster`, và mục "default" ở đó giờ là mùa '17 của chính client (88), nên
  "2017" chỉ còn hiện một lần trong danh sách mùa.
- **Mọi thẻ giao dịch được** đều có trên Market, trong giao dịch và trong gói: một thẻ được tính khi
  fifaaddict có nó hoặc chính game đánh dấu nó giao dịch được (`permid.transferable`). Chỉ dùng fifaaddict
  sẽ thiếu 6.599 thẻ (4.313 thẻ trong đó là '17); Market giờ có 44.703 thẻ. Gói mới **Semua Pemain** (tất
  cả cầu thủ) có thể cho bất kỳ thẻ nào trong số đó.
  Thẻ sự kiện FO3 vốn không giao dịch được cũng tính (Cafu, Kahn, Rivaldo và các huyền thoại mùa 26 khác,
  huyền thoại EL / CEL...): install đánh dấu mọi thẻ trong client là giao dịch được (tìm kiếm Market chỉ hiện
  những thẻ đó), và danh sách mùa của Market có thêm các mùa còn thiếu (26, 32, 38, 41, 57).
  CLB chọn trong bộ lọc tìm kiếm Market cũng hiện thẻ của các cựu cầu thủ (theo lịch sử CLB, bảng playerhistory):
  install vá TransferUtil.teamidQuery trong Module.big.
- **Tim > Klub > Informasi tim** (issue #9) hiện ngày thành lập, sân nhà và "năm xây / sức chứa"
  (`coach.career`, `coach.stadium`, `GET /stadium/<id>` từ bảng sân của client), và bảng cầu thủ (nút
  chuyển, cầu thủ 3D, giá, cấp thẻ), vốn trống khi không có sân. Tên trong cơ sở dữ liệu của client được
  đọc theo UTF-8 (`tools/re/eadb.py` trước đây đọc latin-1).
- **Hiệu ứng sáng khi mở gói** theo giá trị thẻ: không có dưới 10 triệu EP, rồi mức 1-3 từ 10 triệu, 100 triệu
  và 1 tỉ EP.

Lỗi đã biết, chưa giải quyết: khi cài bài hát EA Trax riêng bằng `tools/eatrax_song.py`, game cạn vùng nhớ
RAssets sau mỗi trận, lúc màn hình Cup hoặc sảnh tải lại (crash tại
`FifaRNA::Crowd::PlacementData::Seats[]`); với nhạc gốc của game thì không. Crash y hệt khi bài hát ở dạng PCM
thô (37,7 MB), dạng EALayer3 (6,5 MB, `tools/re/ealayer3.py`) thêm làm bài thứ 10, và dạng EALayer3 thay
TrackID 1 với kho nhạc nhỏ hơn bản gốc, nên kích thước nhạc không phải nguyên nhân. Hãy dùng nhạc gốc của
game: `python -I tools/eatrax_song.py --restore` khôi phục lại.

Chưa làm: chơi online với người khác (xếp hạng PvP, giao hữu, King of the Hill, trận đấu CLB), Road to World, mô phỏng giải đấu.

## Yêu cầu

- Windows, Python 3.12, `pip install cryptography` (cho messenger + trích xuất cơ sở dữ liệu).
- Client FO3 ID đã cài (đường dẫn mặc định `D:\FO3ID\32795`; nếu ở chỗ khác, đặt `FO3_GAME` là thư mục
  đó; máy chủ cảnh báo khi khởi động nếu không tìm thấy bộ nhớ đệm hình ảnh của client ở đó).
- Tùy chọn: ffmpeg (chỉ cho `tools/eatrax_song.py`).

## Cài đặt

**Dễ nhất: setup.exe.** Đóng game rồi chạy `FO3ID-Server-Setup-<phiên bản>.exe`: nó hỏi thư mục game
FO3 ID, cài server kèm Python riêng (vào `C:\FO3ID Server`), tạo chứng chỉ, thêm dòng hosts, trích xuất
cơ sở dữ liệu cầu thủ, vá game (`installer/setup_pc.py`, nhật ký ở `installer/setup.log`) và tạo lối tắt
**FO3ID** trên màn hình: nhấp đúp để chơi. Bên trong không có file game nên game phải được cài sẵn; mùa
FO4, sự nghiệp câu lạc bộ và khuôn mặt 3D có được nhờ các công cụ bên dưới. Gỡ cài đặt sẽ trả lại file
gốc của game. Cách tạo: `python installer/build.py` (cần Inno Setup 6; Python và các gói chỉ tải một lần).

Cài thủ công:

1. Trích xuất cơ sở dữ liệu cầu thủ từ client của bạn:
   `python extract_fifadb.py D:\FO3ID\32795` → `gamedata/fifadb.sqlite`.
2. Giá thị trường (tùy chọn): `python tools/fa_sync.py` → `gamedata/fa_prices.json` (lấy dữ liệu từ
   fifaaddict.com một cách lịch sự; không có nó thì giá tính theo công thức chỉ số).
3. Chứng chỉ TLS cho máy chủ API được client ghi cứng, `APSEODESK172` (chấp nhận tự ký):

   ```
   openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=APSEODESK172" -addext "subjectAltName=DNS:APSEODESK172" -keyout key.pem -out cert.pem
   python -c "open('certs/server.pem','w').write(open('cert.pem').read()+open('key.pem').read())"
   ```

4. File hosts (với quyền quản trị): `127.0.0.1 APSEODESK172`.
5. Build launcher một lần (`launcher/fifalauncher.cs`, lệnh build ghi bên trong), rồi nhấp đúp
   **`play.bat`**: nó mở cửa sổ máy chủ (`python start_all.py` = `http_config.py 443 --tls` +
   `messenger.py`; đóng cửa sổ đó để dừng máy chủ) và chạy game mà không hiện hộp thoại UAC.
   Trước đó nó chạy `tools/fo4_season.py install --if-needed`: khi client chưa có thẻ, CLB, mặt 3D và bản vá của
   máy chủ này (PC khác, client mới, có thay đổi từ lần install trước), nó cài vào (một hai phút, game đóng);
   lần install cuối được ghi trong `fo3id_install.json` ở thư mục game. Chuyển PC: chép cả `gamedata/` và
   `backup/` (không có trong git).

6. Hoặc làm tay: `python start_all.py`, rồi chạy client với các tham số máy chủ (giống những gì
   `launcher/fifalauncher.exe` và `launch_game.ps1` truyền vào):

   ```
   fifazf.exe -QUICKMATCH/SERVER=127.0.0.1:28000 -QUICKMATCH/PROTOCOL=tcp -QUICKMATCH/NOTISERVER=127.0.0.1:28001 -FIFA/LAUNCHER/PID=<pid> -PUBLISHER/GARENA/TOKEN=testtoken -LOCALE/PUBLISHING_REGION=ID -DUMPTRUCK/SERVER=127.0.0.1 region id token testtoken
   ```

## Các file

| File | Vai trò |
|---|---|
| `http_config.py` | Backend REST mà client gọi (`https://APSEODESK172`): đăng nhập, tài khoản, HLV, tạo CLB, đội hình, giải đấu, cúp, thị trường, nâng cấp, giao dịch, cửa hàng, vật phẩm, Bag, quà hằng ngày, bảng tĩnh, và `/cdn/` (phục vụ logo và thẻ từ `_cache` của chính client). Dữ liệu lưu ở `gamedata/state.json`. Biến môi trường: `FO3_LANGUAGE` (mặc định `ind`), `FO3_GAME` (thư mục client), `FO3_ASSETS`, `FO3_HALF_LENGTH`, `FO3_ADMIN_PORT`. |
| `messenger.py` | Máy chủ thông báo ở `:28002`. Khung `<u16 LE len><u16 type=2><Blowfish-CBC(key "kueec", IV 0) of JSON+"\n">`; trả lời `auth`, `ping`, `signon`. `python messenger.py --selftest`. |
| `admin.py` + `admin/index.html` | Trang quản trị (do `http_config.py` khởi động, chỉ truy cập từ máy này, không mật khẩu). Tab Cửa hàng lưu vào `gamedata/shop.json`. Mọi chữ trên trang, kể cả thông báo lỗi, nằm trong các bảng `I18N` ở cuối `admin.py`, mỗi ngôn ngữ một bảng; thêm ngôn ngữ mới chỉ cần thêm một bảng, và `python admin.py` kiểm tra mọi bảng có cùng khóa. |
| `responses/` | Body JSON cho từng endpoint (`/a/b/c` → `responses/a_b_c.json`, đọc lại ở mỗi yêu cầu). Cấu trúc lấy từ mã AS3 của client. |
| `extract_fifadb.py` | Cơ sở dữ liệu cầu thủ của client (`data_0.big`, RefPack + Blowfish) → SQLite. |
| `gen_teams.py` | Đội hình khởi đầu và tóm tắt CLB từ `gamedata/fifadb.sqlite`. |
| `tools/eatrax_song.py` | Đặt một bài hát lên đầu nhạc menu EA Trax dưới dạng EALayer3, thay TrackID 1 (sửa `data_0.big` / `data_1.big` bằng cách ghi nối thêm; `--restore` khôi phục bản gốc). Làm game crash sau trận, xem lỗi đã biết. |
| `tools/fa_sync.py` | Đồng bộ giá từ fifaaddict.com. |
| `tools/fo4_season.py` | Thêm thẻ 26 LIVE và 25 LIVE của FIFA Online 4 (dữ liệu fifaaddict, chỉ số tính là +0) thành hai mùa mới với biểu tượng mùa của FO4: `fetch`, `collect` + `import`, `build`, `install` (ghi đè các mục trong `data_0.big` tại chỗ vì kích thước tệp không được đổi, và ghi nối thêm vào `Label.big` và `Module.big`; `restore` khôi phục tất cả). `hud` chỉ vá `Module.big`, cho client đã cài mùa giải từ trước: ô tên cầu thủ trong trận trước đây hiện huy hiệu '17 cho mọi thẻ 2017 và FO4, giờ hiện mùa của chính thẻ đó. `wc18` lấy 300 cầu thủ hay nhất trong danh sách đội tuyển World Cup 2018 cho `build` (chỉ số FIFA 18 World Cup của futwiz, danh sách và câu lạc bộ từ Wikipedia): mùa WC18 (mã 78), thẻ ở +1 bằng chỉ số World Cup - 5, với biểu tượng "18" màu xanh lá như biểu tượng World Cup của chính FO3; team color "2018 WC All-Star" (mùa) và "18 WC <quốc gia>" (thẻ của từng quốc gia), đặc tính lấy từ thẻ FO3 và thẻ FIFA 18 của cầu thủ. |
| `tools/fa_faces.py` | Thêm ảnh mặt nhỏ của cầu thủ còn thiếu trong bộ nhớ đệm của client (fifaaddict có cùng loại PNG 128x128); `--crests` thêm logo CLB còn thiếu (đa số CLB không có trong bộ nhớ đệm nên game hiện logo mặc định); `--staffs` thêm ảnh staff (bộ nhớ đệm chỉ có 7 trong 71 staff máy chủ phát). Chạy tiếp được nếu bị ngắt, `--remove` hoàn tác. Ảnh cầu thủ fifaaddict không có sẽ lấy từ FC Online (FO4); `--all` lấy mọi loại. |
| `tools/club_history.py` | Sự nghiệp CLB đến mùa hiện tại cho tab Karakteristik trong thông tin cầu thủ: cầu thủ trên Wikidata được ghép với cầu thủ FO3 theo ngày sinh và tên, các CLB (P54: năm, cho mượn; bỏ đội tuyển và đội trẻ) được ghép với ID đội của EA (lịch sử FO3, rồi tên trong TEAMNAMES, nếu không có thì ID mới có tên trong TEAMNAMES), lưu vào `gamedata/club_history.json`; `fo4_season.py install` đưa vào client. `show TÊN` in lịch sử của một cầu thủ. |
| `tools/photo_head.py` | Khuôn mặt 3D từ ảnh: đọc và ghi các tệp đầu quét của client (RX3 / chunkzip, texture mặt DXT1 512×512), uốn ảnh lên texture mặt theo 11 điểm rồi hòa vào (OpenCV), và dựng lại `data_7.big` với các tệp đầu từ ảnh (mesh và tóc của người cho với id đầu mới từ 99.000.000). `uv HEADASSETID OUT.png` lưu texture mặt của một đầu. Các điểm có thể lấy từ YuNet (`landmarks`) và người cho từ `donor_looks` / `suggest`. |
| `tools/draft_icons.py` | Vẽ biểu tượng Shop cho các gói draft (bộ nhớ đệm của client không có ảnh draft) vào `assets/externalAssets/shop/`, máy chủ gửi các ảnh này trước ảnh của client; `<icon> <tag> <picks>` vẽ thêm một biểu tượng cho gói tạo trên trang admin. Cần Pillow. |
| `tools/re/` | Công cụ hỗ trợ dịch ngược: `bigdir.py` (bảng lưu trữ BIg4), `refpack.py` (codec RefPack), `eadb.py` (bảng cơ sở dữ liệu "DB" của EA), `abc*.py` (phân tích / tìm kiếm / khai thác cấu trúc bytecode AS3), `gfxx.py` (SWF giao diện), `memgrab.py`, `dbwin.py` (đầu ra debug của client), `relay_emu.py` (bộ mã hóa gói relay PvP và relay thử nghiệm), `gamectl.ps1` (điều khiển cửa sổ client khi kiểm thử). |
| `blaze.py`, `server.py`, `test_client.py`, `analyze_capture.py` | Khung EA Blaze (Fire/TDF) và máy chủ kiểm thử loopback từ giai đoạn đầu của dự án; sau đó hóa ra client dùng REST + messenger. |
| `analysis/` | Các script phân tích client ban đầu (chuỗi, section PE, endpoint mạng). |
| `FO3ID-private-server.md`, `reports/` | Phát hiện và báo cáo tiến độ (`reports/` viết bằng tiếng Indonesia). |

## Kiểm thử

```
python run_tests.py
```

Chạy tự kiểm thử Blaze/loopback và `test_league.py`, file chạy qua mọi tính năng backend (tạo CLB, giải
đấu, cúp, thị trường, nâng cấp, giao dịch, cửa hàng, quà hằng ngày, API quản trị, xóa HLV) trên một tài
khoản mới. Cần `gamedata/fifadb.sqlite`.

## Người đóng góp

- **SoloPlayer** — chủ dự án, kiểm thử trong client thật.
- **Claude** (trợ lý AI của Anthropic, qua Claude Code) — dịch ngược và phần lớn mã máy chủ, viết cùng
  chủ dự án.

## Giấy phép

[GNU GPL v3](LICENSE) cho mã và tài liệu trong kho này. FIFA Online 3, client, dữ liệu, tài nguyên và
nhạc của game thuộc về chủ sở hữu tương ứng và không nằm trong giấy phép này.
