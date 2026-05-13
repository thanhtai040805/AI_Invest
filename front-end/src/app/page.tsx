import Image from 'next/image';
import Link from 'next/link';

export default function Home() {
    return (
        <div className="font-body-lg text-body-lg selection:bg-primary/30">


            <header className="fixed top-0 w-full z-50 flex justify-between items-center px-lg h-20 bg-surface/40 backdrop-blur-xl border-b border-white/10 shadow-[0_20px_40px_rgba(0,0,0,0.3)]">
                <div className="flex items-center gap-md">
                    <span className="font-display-lg text-display-lg font-bold text-primary tracking-tighter">AIInvest</span>
                </div>
                <nav className="hidden md:flex items-center gap-lg">
                    <Link className="text-primary border-b-2 border-primary pb-1 font-title-md text-title-md" href="/dashboard">Dashboard</Link>
                    <Link className="text-on-surface-variant hover:text-on-surface transition-colors font-title-md text-title-md" href="/advanced-chart">Trading</Link>
                    <Link className="text-on-surface-variant hover:text-on-surface transition-colors font-title-md text-title-md" href="/ai-assistant">AI Assistant</Link>
                    <Link className="text-on-surface-variant hover:text-on-surface transition-colors font-title-md text-title-md" href="/community">Community</Link>
                    <Link className="text-on-surface-variant hover:text-on-surface transition-colors font-title-md text-title-md" href="/simulator">Simulator</Link>
                </nav>
                <div className="flex items-center gap-md">
                    <button className="material-symbols-outlined text-on-surface-variant hover:bg-white/5 transition-all p-2 rounded-full">notifications</button>
                    <button className="material-symbols-outlined text-on-surface-variant hover:bg-white/5 transition-all p-2 rounded-full">settings</button>
                    <div className="w-10 h-10 rounded-full overflow-hidden border border-primary/20">
                        <img alt="User Profile Avatar" className="w-full h-full object-cover" src="https://lh3.googleusercontent.com/aida-public/AB6AXuCQjhFJa2MnOgj1rsDkx4cPoM93-nocc7siFyH7XmolXc3ENADM0FYz-hxtxb8FAIl2kTOfSBuLzT94M3SXsE77I7OlBbtVxyOB0HRqpv5i-ij-v2KVNZ8Oj8yA_4jXW058gtO8syaejnJdYV3N8augGsZEQuGR43fS3Ezs5BSZRjlV9JZ1nNob2inFpbjKpmLIgfETfdIBZ-oiNTUFzVTh8pvX44dQ3xitaSa3QyK_WabXUf6Vl3XqPBoAQ_t56k65ybsFb7nzUJfx" />
                    </div>
                </div>
            </header>
            <main className="pt-20">

                <section className="relative min-h-[921px] flex items-center justify-center overflow-hidden px-md">
                    <div className="absolute inset-0 z-0">
                        <div className="absolute top-1/4 left-1/4 w-[500px] h-[500px] bg-primary/10 blur-[120px] rounded-full"></div>
                        <div className="absolute bottom-1/4 right-1/4 w-[600px] h-[600px] bg-secondary/5 blur-[150px] rounded-full"></div>
                    </div>
                    <div className="container-max mx-auto grid lg:grid-cols-2 gap-xl items-center relative z-10">
                        <div className="space-y-lg text-center lg:text-left">
                            <div className="inline-flex items-center gap-xs px-md py-base glass-card rounded-full border-primary/20">
                                <span className="w-2 h-2 rounded-full bg-secondary animate-pulse"></span>
                                <span className="font-label-caps text-label-caps text-secondary uppercase">Live Vietnamese Market Data</span>
                            </div>
                            <h1 className="font-display-lg text-[56px] leading-[1.05] font-bold tracking-tight">
                                Hệ Sinh Thái Đầu Tư <span className="text-gradient-primary">AI Toàn Diện.</span>
                            </h1>
                            <p className="text-on-surface-variant text-xl mx-auto lg:mx-0">
                                Kết hợp giao dịch chứng khoán chuyên nghiệp, trợ lý AI thông minh và cộng đồng đầu tư xã hội hàng đầu Việt Nam.
                            </p>
                            <div className="flex flex-col sm:flex-row items-center gap-md justify-center lg:justify-start pt-md">
                                <button className="px-xl py-md bg-primary-container text-on-primary-container font-title-md text-title-md rounded-xl hover:scale-105 transition-all shadow-lg ai-glow">
                                    Bắt đầu ngay
                                </button>
                                <button className="px-xl py-md glass-card text-on-surface font-title-md text-title-md rounded-xl hover:bg-white/10 transition-all">
                                    Xem Dashboard
                                </button>
                            </div>
                            <div className="flex items-center gap-xl pt-lg justify-center lg:justify-start opacity-70">
                                <div className="flex flex-col">
                                    <span className="font-data-mono text-xl text-secondary">VN-INDEX 1,284.12</span>
                                    <span className="font-label-caps text-label-caps text-secondary">+12.45 (0.98%)</span>
                                </div>
                                <div className="w-px h-10 bg-white/10"></div>
                                <div className="flex flex-col">
                                    <span className="font-data-mono text-xl text-on-surface">VN30 1,298.45</span>
                                    <span className="font-label-caps text-label-caps text-secondary">+8.22 (0.64%)</span>
                                </div>
                            </div>
                        </div>
                        <div className="relative group">
                            <div className="absolute -inset-1 bg-gradient-to-r from-primary to-secondary rounded-2xl blur opacity-25 group-hover:opacity-40 transition duration-1000"></div>
                            <div className="relative glass-card rounded-2xl overflow-hidden shadow-2xl border border-white/10">
                                <img alt="AI Dashboard Preview" className="w-full aspect-[4/3] object-cover opacity-90" data-alt="A futuristic 3D visualization of a stock market dashboard featuring complex line graphs showing the VN-Index trends. The interface is high-fidelity glassmorphism with glowing emerald green and electric blue accents. Geometric AI nodes float around the screen in a deep graphite background with soft, luminous highlights." src="https://lh3.googleusercontent.com/aida-public/AB6AXuBqC4XMa2RjHuap5Xx8kN4PUCeWnIyF_ThCf0dsZLNZxKhdFBEAWL0DFDZu1hx3VBc_o4mF9hDmqfn26Xp0bp_wDQqXECGhsfVTSyLdsoY2jKyiSqeeb1kbPTi-x95dn1PZo4dzsO5AtcH7WIwC7397RMy_unSKmqyLBjJaMsWT9SjSNPrZkX4NA_gWO8Drep9iRogsh81FNb7IS3PSUGJkQ3AOL3XeijaOES-4CIv4n6WavkmGKkubbap8yX4AAJP3DkHIpAnQNSVo" />
                                <div className="absolute bottom-0 left-0 right-0 p-lg bg-gradient-to-t from-black/80 to-transparent">
                                    <div className="flex items-center justify-between">
                                        <span className="font-title-md text-title-md">Smart Portfolios</span>
                                        <span className="material-symbols-outlined text-primary">trending_up</span>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </section>

                <section className="py-xl px-md bg-surface-container-lowest/50">
                    <div className="container-max mx-auto">
                        <div className="max-w-3xl mx-auto text-center mb-xl">
                            <h2 className="font-headline-lg text-headline-lg mb-md">Trợ Lý AI Cá Nhân Hóa</h2>
                            <p className="text-on-surface-variant">Phân tích chuyên sâu về thị trường Việt Nam trong nháy mắt.</p>
                        </div>
                        <div className="grid lg:grid-cols-5 gap-lg items-center">
                            <div className="lg:col-span-3 glass-card p-lg rounded-2xl space-y-md border border-white/5 shadow-xl relative overflow-hidden">
                                <div className="flex items-center gap-md border-b border-white/10 pb-md">
                                    <div className="w-10 h-10 rounded-full bg-primary/20 flex items-center justify-center">
                                        <span className="material-symbols-outlined text-primary" data-weight="fill">smart_toy</span>
                                    </div>
                                    <div>
                                        <h4 className="font-title-md text-title-md">AI Portfolio Assistant</h4>
                                        <p className="text-[10px] uppercase tracking-widest text-secondary font-label-caps">Active • GPT-4o Enhanced</p>
                                    </div>
                                </div>
                                <div className="space-y-lg py-md">
                                    <div className="flex gap-md">
                                        <div className="w-8 h-8 rounded-full bg-surface-variant flex-shrink-0"></div>
                                        <div className="bg-surface-variant/50 p-md rounded-xl rounded-tl-none max-w-[80%]">
                                            <p className="text-body-sm text-on-surface-variant">Tôi nên mua VCB hay HPG vào thời điểm này?</p>
                                        </div>
                                    </div>
                                    <div className="flex gap-md flex-row-reverse">
                                        <div className="w-8 h-8 rounded-full bg-primary/20 flex items-center justify-center flex-shrink-0">
                                            <span className="material-symbols-outlined text-primary text-sm" data-weight="fill">smart_toy</span>
                                        </div>
                                        <div className="bg-primary-container/10 p-md rounded-xl rounded-tr-none max-w-[85%] border border-primary/20">
                                            <p className="text-body-sm text-on-surface">Dựa trên dữ liệu HOSE hôm nay, <span className="text-secondary font-bold">VCB</span> đang giữ nền giá tốt tại 91.5 với hỗ trợ mạnh. <span className="text-secondary font-bold">HPG</span> đang chịu áp lực bán từ khối ngoại nhưng định giá P/B vẫn hấp dẫn. AI khuyên bạn có thể giải ngân 30% tỷ trọng vào VCB để phòng thủ.</p>
                                            <div className="mt-md pt-md border-t border-white/10 flex gap-md">
                                                <div className="bg-black/20 p-sm rounded-lg flex-1 border border-white/5">
                                                    <span className="block text-[10px] text-on-surface-variant">VCB Momentum</span>
                                                    <span className="text-secondary font-data-mono">Strong Buy</span>
                                                </div>
                                                <div className="bg-black/20 p-sm rounded-lg flex-1 border border-white/5">
                                                    <span className="block text-[10px] text-on-surface-variant">HPG Trend</span>
                                                    <span className="text-tertiary font-data-mono">Neutral</span>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <div className="flex items-center gap-md pt-md border-t border-white/10">
                                    <input className="flex-1 bg-black/40 border border-white/10 rounded-xl px-md py-sm focus:ring-1 focus:ring-primary outline-none text-body-sm" placeholder="Hỏi AI về mã cổ phiếu..." type="text" />
                                    <button className="w-10 h-10 rounded-xl bg-primary flex items-center justify-center text-on-primary">
                                        <span className="material-symbols-outlined">send</span>
                                    </button>
                                </div>
                            </div>
                            <div className="lg:col-span-2 space-y-md">
                                <div className="glass-card p-md rounded-xl border-l-4 border-l-secondary">
                                    <h5 className="font-title-md text-title-md text-secondary">Phân tích Real-time</h5>
                                    <p className="text-body-sm text-on-surface-variant">AI theo dõi hàng nghìn biến động giá mỗi giây trên HOSE, HNX, UPCOM.</p>
                                </div>
                                <div className="glass-card p-md rounded-xl">
                                    <h5 className="font-title-md text-title-md">Cảnh báo rủi ro</h5>
                                    <p className="text-body-sm text-on-surface-variant">Tự động phát hiện các tín hiệu đảo chiều hoặc áp lực bán đột biến.</p>
                                </div>
                                <div className="glass-card p-md rounded-xl">
                                    <h5 className="font-title-md text-title-md">Tối ưu hóa thuế</h5>
                                    <p className="text-body-sm text-on-surface-variant">Tính toán chi phí giao dịch và thuế TNCN tự động cho nhà đầu tư.</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </section>

                <section className="py-xl px-md">
                    <div className="container-max mx-auto">
                        <div className="flex flex-col md:flex-row md:items-end justify-between mb-xl gap-md">
                            <div>
                                <h2 className="font-headline-lg text-headline-lg">Trải Nghiệm Trading Đẳng Cấp</h2>
                                <p className="text-on-surface-variant">Giao diện hiện đại được thiết kế riêng cho thị trường Việt Nam.</p>
                            </div>
                            <Link className="text-primary flex items-center gap-xs font-title-md text-title-md" href="/dashboard">Khám phá toàn bộ tính năng <span className="material-symbols-outlined">arrow_forward</span></Link>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-4 grid-rows-2 gap-lg h-[600px]">
                            <div className="md:col-span-2 md:row-span-2 glass-card rounded-2xl overflow-hidden relative">
                                <img alt="Main Dashboard" className="w-full h-full object-cover opacity-60" data-alt="A cinematic wide shot of a high-tech trading terminal with multiple glowing data visualizations. The central monitor shows a detailed candlestick chart for the VN-Index. The surrounding environment is dark, reflecting the electric blue and emerald green lights from the screens. Soft gold bokeh effects highlight AI-generated investment tips floating in the air." src="https://lh3.googleusercontent.com/aida-public/AB6AXuDCNyqXyobKfuflJqPoZQr_iMkVz4vpJ6zwWp3XopReOlVQNzwwMYoQ92BTOxScsndswyZOArMXM0Z_Erd0cgNSxD7bb3U3DIJSEpndAHg_F4OyYCHUk8mzgkpqJMj6fzGJHdn4vEYcQ6xCfzPmkf21o5XUDQLXEA67dIIudINGccWZB-LY37tQ2L-Ee0A4BvkClygTMQln5iHTedf19Z8LjVCCkw04pP-EgxT0KRpo-Rc_AZ5FLr9_TJvpBSluaebAqxQKIU30fBAc" />
                                <div className="absolute inset-0 p-lg flex flex-col justify-between">
                                    <div className="flex justify-between items-start">
                                        <span className="bg-primary/20 text-primary px-sm py-1 rounded-full text-[10px] font-bold uppercase tracking-wider">Professional Interface</span>
                                        <div className="flex gap-xs">
                                            <span className="w-3 h-3 rounded-full bg-red-500/50"></span>
                                            <span className="w-3 h-3 rounded-full bg-yellow-500/50"></span>
                                            <span className="w-3 h-3 rounded-full bg-green-500/50"></span>
                                        </div>
                                    </div>
                                    <div className="bg-black/60 backdrop-blur-md p-md rounded-xl border border-white/10">
                                        <h3 className="font-title-md text-title-md mb-xs">Biểu đồ kỹ thuật đa lớp</h3>
                                        <p className="text-body-sm text-on-surface-variant">Tích hợp TradingView với các chỉ số AI độc quyền chỉ có tại AIInvest.</p>
                                    </div>
                                </div>
                            </div>
                            <div className="md:col-span-2 glass-card rounded-2xl overflow-hidden relative">
                                <img alt="Portfolio Tracker" className="w-full h-full object-cover opacity-40" data-alt="Close up of a mobile smartphone screen displaying a sleek finance application with a modern UI. The app shows a vibrant portfolio distribution chart in electric blue and white. The background is a blurred office at night, emphasizing a professional and elite lifestyle. Minimalist and clean aesthetic with high contrast." src="https://lh3.googleusercontent.com/aida-public/AB6AXuCdj70RlfVgnSa3sCnAe_DJPCL8uEO_On59PTIv8yhqRnpN4pInm4KNo7T7BbbfENrxp_H2jaOmxNceSsC97zw43e7co7yDXefTeApb67RO8IkP_mG8OHgLw0W9ANozIYzmrI896ADcA-5QvV4fjqiVOP96MnuV8kHN1QT772tBHTIlDqDTDE-OloT00oZMfp1-c3eVEPibmPi3_NcwYxK9ZGrM0V6bp8x2uuJpIor0fm1VPBO1zUNUqhkMCY-YJbQazEpimugPzSwD" />
                                <div className="absolute inset-0 p-lg">
                                    <h3 className="font-title-md text-title-md">Quản lý danh mục thông minh</h3>
                                    <p className="text-body-sm text-on-surface-variant">Tự động phân bổ tài sản dựa trên khẩu vị rủi ro.</p>
                                </div>
                            </div>
                            <div className="glass-card rounded-2xl p-lg flex flex-col justify-between">
                                <span className="material-symbols-outlined text-secondary text-4xl">group</span>
                                <div>
                                    <h4 className="font-title-md text-title-md">Cộng đồng VIP</h4>
                                    <p className="text-body-sm text-on-surface-variant">Sao chép lệnh từ các chuyên gia hàng đầu.</p>
                                </div>
                            </div>
                            <div className="glass-card rounded-2xl p-lg flex flex-col justify-between">
                                <span className="material-symbols-outlined text-tertiary text-4xl">verified_user</span>
                                <div>
                                    <h4 className="font-title-md text-title-md">Bảo mật PCI-DSS</h4>
                                    <p className="text-body-sm text-on-surface-variant">An toàn tuyệt đối theo tiêu chuẩn quốc tế.</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </section>

                <section className="py-xl px-md bg-surface-container-lowest">
                    <div className="container-max mx-auto">
                        <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-lg">
                            <div className="p-lg glass-card rounded-2xl hover:translate-y-[-8px] transition-all duration-300">
                                <div className="w-12 h-12 rounded-xl bg-primary/10 flex items-center justify-center mb-md">
                                    <span className="material-symbols-outlined text-primary text-3xl">analytics</span>
                                </div>
                                <h3 className="font-title-md text-title-md mb-sm">AI Analysis</h3>
                                <p className="text-body-sm text-on-surface-variant">Hệ thống AI tự động chấm điểm cổ phiếu theo mô hình CANSLIM và VSA.</p>
                            </div>
                            <div className="p-lg glass-card rounded-2xl hover:translate-y-[-8px] transition-all duration-300">
                                <div className="w-12 h-12 rounded-xl bg-secondary/10 flex items-center justify-center mb-md">
                                    <span className="material-symbols-outlined text-secondary text-3xl">sports_esports</span>
                                </div>
                                <h3 className="font-title-md text-title-md mb-sm">Virtual Trading</h3>
                                <p className="text-body-sm text-on-surface-variant">Thực hành đầu tư với 1 tỷ VNĐ tiền ảo nhưng dữ liệu thật 100%.</p>
                            </div>
                            <div className="p-lg glass-card rounded-2xl hover:translate-y-[-8px] transition-all duration-300">
                                <div className="w-12 h-12 rounded-xl bg-tertiary/10 flex items-center justify-center mb-md">
                                    <span className="material-symbols-outlined text-tertiary text-3xl">dynamic_feed</span>
                                </div>
                                <h3 className="font-title-md text-title-md mb-sm">Social Feed</h3>
                                <p className="text-body-sm text-on-surface-variant">Cập nhật tin tức vĩ mô và nhận định từ cộng đồng nhà đầu tư tinh hoa.</p>
                            </div>
                            <div className="p-lg glass-card rounded-2xl hover:translate-y-[-8px] transition-all duration-300">
                                <div className="w-12 h-12 rounded-xl bg-primary-container/10 flex items-center justify-center mb-md">
                                    <span className="material-symbols-outlined text-primary-container text-3xl">account_balance_wallet</span>
                                </div>
                                <h3 className="font-title-md text-title-md mb-sm">Portfolio Tracking</h3>
                                <p className="text-body-sm text-on-surface-variant">Tổng hợp tài sản từ nhiều tài khoản chứng khoán về một nơi duy nhất.</p>
                            </div>
                        </div>
                    </div>
                </section>
            </main>

            <footer className="bg-surface-container-lowest border-t border-white/10 pt-xl pb-lg px-md">
                <div className="container-max mx-auto grid grid-cols-1 md:grid-cols-4 gap-xl mb-xl">
                    <div className="md:col-span-1 space-y-md">
                        <span className="font-display-lg text-display-lg font-bold text-primary tracking-tighter">AIInvest</span>
                        <p className="text-body-sm text-on-surface-variant">Tiên phong công nghệ AI trong lĩnh vực đầu tư tài chính tại Việt Nam. Giúp nhà đầu tư đưa ra quyết định dựa trên dữ liệu, không cảm xúc.</p>
                        <div className="flex gap-md">
                            <a className="material-symbols-outlined p-2 glass-card rounded-full text-on-surface-variant hover:text-primary" href="#">facebook</a>
                            <a className="material-symbols-outlined p-2 glass-card rounded-full text-on-surface-variant hover:text-primary" href="#">smart_display</a>
                            <a className="material-symbols-outlined p-2 glass-card rounded-full text-on-surface-variant hover:text-primary" href="#">alternate_email</a>
                        </div>
                    </div>
                    <div className="space-y-md">
                        <h5 className="font-title-md text-title-md">Sản phẩm</h5>
                        <ul className="space-y-sm">
                            <li><a className="text-body-sm text-on-surface-variant hover:text-primary transition-colors" href="#">Giao dịch cơ sở</a></li>
                            <li><a className="text-body-sm text-on-surface-variant hover:text-primary transition-colors" href="#">Chứng khoán phái sinh</a></li>
                            <li><a className="text-body-sm text-on-surface-variant hover:text-primary transition-colors" href="#">Trợ lý AI Copilot</a></li>
                            <li><a className="text-body-sm text-on-surface-variant hover:text-primary transition-colors" href="#">Đầu tư mô phỏng</a></li>
                        </ul>
                    </div>
                    <div className="space-y-md">
                        <h5 className="font-title-md text-title-md">Hỗ trợ</h5>
                        <ul className="space-y-sm">
                            <li><a className="text-body-sm text-on-surface-variant hover:text-primary transition-colors" href="#">Trung tâm hỗ trợ</a></li>
                            <li><a className="text-body-sm text-on-surface-variant hover:text-primary transition-colors" href="#">Biểu phí giao dịch</a></li>
                            <li><a className="text-body-sm text-on-surface-variant hover:text-primary transition-colors" href="#">Hướng dẫn mở tài khoản</a></li>
                            <li><a className="text-body-sm text-on-surface-variant hover:text-primary transition-colors" href="#">Chính sách bảo mật</a></li>
                        </ul>
                    </div>
                    <div className="space-y-md">
                        <h5 className="font-title-md text-title-md">Địa chỉ</h5>
                        <p className="text-body-sm text-on-surface-variant">Tầng 45, Tòa nhà Landmark 81, Vinhomes Central Park, Quận Bình Thạnh, TP. Hồ Chí Minh.</p>
                        <p className="text-body-sm text-on-surface-variant">Hotline: 1900 8888</p>
                        <p className="text-body-sm text-on-surface-variant">Email: contact@aiinvest.vn</p>
                    </div>
                </div>
                <div className="container-max mx-auto pt-lg border-t border-white/5 flex flex-col md:flex-row justify-between items-center gap-md opacity-60">
                    <p className="text-[10px] uppercase tracking-widest font-label-caps">© 2024 AIInvest Elite. All Rights Reserved.</p>
                    <p className="text-[10px] text-center">Cảnh báo rủi ro: Đầu tư chứng khoán luôn tiềm ẩn rủi ro. Các nhận định từ AI chỉ mang tính chất tham khảo, không phải là lời khuyên đầu tư tài chính chính thức.</p>
                </div>
            </footer>

        </div>
    );
}
