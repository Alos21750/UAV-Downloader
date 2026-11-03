"""Display-name localization for site-provided browse labels."""

from urllib.parse import urlencode

from uav_downloader.i18n.locales import get_lang


TAG_GROUPS = {
    '衣著': {'en': 'Clothing', 'zh': '衣著', 'zh-Hans': '服装', 'ja': '服装'},
    '身材': {'en': 'Body', 'zh': '身材', 'zh-Hans': '身材', 'ja': 'スタイル'},
    '交合': {'en': 'Acts', 'zh': '交合', 'zh-Hans': '交合', 'ja': 'プレイ'},
    '玩法': {'en': 'Kinks', 'zh': '玩法', 'zh-Hans': '玩法', 'ja': 'プレイ種類'},
    '劇情': {'en': 'Story', 'zh': '劇情', 'zh-Hans': '剧情', 'ja': 'シチュ'},
    '角色': {'en': 'Roles', 'zh': '角色', 'zh-Hans': '角色', 'ja': '役柄'},
    '地點': {'en': 'Places', 'zh': '地點', 'zh-Hans': '地点', 'ja': '場所'},
    '雜項': {'en': 'Misc', 'zh': '雜項', 'zh-Hans': '杂项', 'ja': 'その他'},
}


TAGS = {
    'black-pantyhose': {'en': 'Black Pantyhose', 'zh': '黑絲', 'zh-Hans': '黑丝', 'ja': '黒パンスト'},
    'knee-socks': {'en': 'Knee Socks', 'zh': '過膝襪', 'zh-Hans': '过膝袜', 'ja': 'ニーソックス'},
    'sportswear': {'en': 'Sportswear', 'zh': '運動裝', 'zh-Hans': '运动装', 'ja': 'スポーツウェア'},
    'flesh-toned-pantyhose': {'en': 'Nude Pantyhose', 'zh': '肉絲', 'zh-Hans': '肉丝', 'ja': '肌色パンスト'},
    'pantyhose': {'en': 'Pantyhose', 'zh': '絲襪', 'zh-Hans': '丝袜', 'ja': 'パンスト'},
    'glasses': {'en': 'Glasses', 'zh': '眼鏡娘', 'zh-Hans': '眼镜娘', 'ja': 'メガネ'},
    'kemonomimi': {'en': 'Animal Ears', 'zh': '獸耳', 'zh-Hans': '兽耳', 'ja': 'ケモ耳'},
    'fishnets': {'en': 'Fishnets', 'zh': '漁網', 'zh-Hans': '渔网', 'ja': '網タイツ'},
    'swimsuit': {'en': 'Swimsuit', 'zh': '水着', 'zh-Hans': '泳装', 'ja': '水着'},
    'school-uniform': {'en': 'School Uniform', 'zh': '校服', 'zh-Hans': '校服', 'ja': '制服'},
    'cheongsam': {'en': 'Cheongsam', 'zh': '旗袍', 'zh-Hans': '旗袍', 'ja': 'チャイナドレス'},
    'wedding-dress': {'en': 'Wedding Dress', 'zh': '婚紗', 'zh-Hans': '婚纱', 'ja': 'ウェディングドレス'},
    'maid': {'en': 'Maid', 'zh': '女僕', 'zh-Hans': '女仆', 'ja': 'メイド'},
    'kimono': {'en': 'Kimono', 'zh': '和服', 'zh-Hans': '和服', 'ja': '着物'},
    'stockings': {'en': 'Garter Stockings', 'zh': '吊帶襪', 'zh-Hans': '吊带袜', 'ja': 'ガーターストッキング'},
    'bunny-girl': {'en': 'Bunny Girl', 'zh': '兔女郎', 'zh-Hans': '兔女郎', 'ja': 'バニーガール'},
    'Cosplay': {'en': 'Cosplay', 'zh': 'Cosplay', 'zh-Hans': 'Cosplay', 'ja': 'Cosplay'},
    'suntan': {'en': 'Tan', 'zh': '黑肉', 'zh-Hans': '黑皮', 'ja': '日焼け'},
    'tall': {'en': 'Tall', 'zh': '長身', 'zh-Hans': '高挑', 'ja': '長身'},
    'flexible-body': {'en': 'Flexible Body', 'zh': '軟體', 'zh-Hans': '软体', 'ja': '軟体'},
    'small-tits': {'en': 'Small Tits', 'zh': '貧乳', 'zh-Hans': '贫乳', 'ja': '貧乳'},
    'beautiful-leg': {'en': 'Beautiful Legs', 'zh': '美腿', 'zh-Hans': '美腿', 'ja': '美脚'},
    'beautiful-butt': {'en': 'Beautiful Butt', 'zh': '美尻', 'zh-Hans': '美臀', 'ja': '美尻'},
    'tattoo': {'en': 'Tattoo', 'zh': '紋身', 'zh-Hans': '纹身', 'ja': 'タトゥー'},
    'short-hair': {'en': 'Short Hair', 'zh': '短髮', 'zh-Hans': '短发', 'ja': 'ショートヘア'},
    'hairless-pussy': {'en': 'Hairless', 'zh': '白虎', 'zh-Hans': '白虎', 'ja': 'パイパン'},
    'mature-woman': {'en': 'Mature', 'zh': '熟女', 'zh-Hans': '熟女', 'ja': '熟女'},
    'big-tits': {'en': 'Big Tits', 'zh': '巨乳', 'zh-Hans': '巨乳', 'ja': '巨乳'},
    'girl': {'en': 'Girl', 'zh': '少女', 'zh-Hans': '少女', 'ja': '少女'},
    'dainty': {'en': 'Petite', 'zh': '嬌小', 'zh-Hans': '娇小', 'ja': '小柄'},
    'facial': {'en': 'Facial', 'zh': '顏射', 'zh-Hans': '颜射', 'ja': '顔射'},
    'footjob': {'en': 'Footjob', 'zh': '腳交', 'zh-Hans': '脚交', 'ja': '足コキ'},
    'anal-sex': {'en': 'Anal Sex', 'zh': '肛交', 'zh-Hans': '肛交', 'ja': 'アナル'},
    'spasms': {'en': 'Spasms', 'zh': '痙攣', 'zh-Hans': '痉挛', 'ja': '痙攣'},
    'squirting': {'en': 'Squirting', 'zh': '潮吹', 'zh-Hans': '潮吹', 'ja': '潮吹き'},
    'deep-throat': {'en': 'Deep Throat', 'zh': '深喉', 'zh-Hans': '深喉', 'ja': 'ディープスロート'},
    'kiss': {'en': 'Kiss', 'zh': '接吻', 'zh-Hans': '接吻', 'ja': 'キス'},
    'cum-in-mouth': {'en': 'Oral Cumshot', 'zh': '口爆', 'zh-Hans': '口爆', 'ja': '口内射精'},
    'blowjob': {'en': 'Blowjob', 'zh': '口交', 'zh-Hans': '口交', 'ja': 'フェラ'},
    'tit-wank': {'en': 'Titjob', 'zh': '乳交', 'zh-Hans': '乳交', 'ja': 'パイズリ'},
    'creampie': {'en': 'Creampie', 'zh': '中出', 'zh-Hans': '中出', 'ja': '中出し'},
    'outdoor': {'en': 'Outdoor Exposure', 'zh': '露出', 'zh-Hans': '露出', 'ja': '露出'},
    'gang-intrusion': {'en': 'Gang Intrusion', 'zh': '集團進犯', 'zh-Hans': '集团进犯', 'ja': '集団侵入'},
    'intrusion': {'en': 'Intrusion', 'zh': '進犯', 'zh-Hans': '进犯', 'ja': '侵入'},
    'tune': {'en': 'Training', 'zh': '調教', 'zh-Hans': '调教', 'ja': '調教'},
    'bondage': {'en': 'Bondage', 'zh': '綑綁', 'zh-Hans': '捆绑', 'ja': '緊縛'},
    'quickie': {'en': 'Instant Penetration', 'zh': '瞬間插入', 'zh-Hans': '瞬间插入', 'ja': '即ハメ'},
    'chikan': {'en': 'Chikan', 'zh': '痴漢', 'zh-Hans': '痴汉', 'ja': '痴漢'},
    'chizyo': {'en': 'Chijo', 'zh': '痴女', 'zh-Hans': '痴女', 'ja': '痴女'},
    'masochism-guy': {'en': 'Male Masochist', 'zh': '男M', 'zh-Hans': '男M', 'ja': '男性M'},
    'crapulence': {'en': 'Drunk', 'zh': '泥醉', 'zh-Hans': '泥醉', 'ja': '泥酔'},
    'soapland': {'en': 'Soapland', 'zh': '泡姬', 'zh-Hans': '泡姬', 'ja': 'ソープ'},
    'breast-milk': {'en': 'Breast Milk', 'zh': '母乳', 'zh-Hans': '母乳', 'ja': '母乳'},
    'piss': {'en': 'Piss', 'zh': '放尿', 'zh-Hans': '放尿', 'ja': '放尿'},
    'massage': {'en': 'Massage', 'zh': '按摩', 'zh-Hans': '按摩', 'ja': 'マッサージ'},
    'groupsex': {'en': 'Group', 'zh': '多P', 'zh-Hans': '多P', 'ja': '複数プレイ'},
    'grip': {'en': 'Restraints', 'zh': '刑具', 'zh-Hans': '刑具', 'ja': '拘束具'},
    'insult': {'en': 'Humiliation', 'zh': '凌辱', 'zh-Hans': '凌辱', 'ja': '凌辱'},
    '10-times-a-day': {'en': '10 Times a Day', 'zh': '一日十回', 'zh-Hans': '一日十次', 'ja': '一日十回'},
    '3p': {'en': '3P', 'zh': '3P', 'zh-Hans': '3P', 'ja': '3P'},
    'black': {'en': 'Black Man', 'zh': '黑人', 'zh-Hans': '黑人', 'ja': '黒人'},
    'ugly-man': {'en': 'Ugly Man', 'zh': '醜男', 'zh-Hans': '丑男', 'ja': 'ブサメン'},
    'temptation': {'en': 'Temptation', 'zh': '誘惑', 'zh-Hans': '诱惑', 'ja': '誘惑'},
    'kinship': {'en': 'Relatives', 'zh': '親屬', 'zh-Hans': '亲属', 'ja': '親族'},
    'virginity': {'en': 'Virginity', 'zh': '童貞', 'zh-Hans': '童贞', 'ja': '童貞'},
    'time-stop': {'en': 'Time Stop', 'zh': '時間停止', 'zh-Hans': '时间停止', 'ja': '時間停止'},
    'avenge': {'en': 'Revenge', 'zh': '復仇', 'zh-Hans': '复仇', 'ja': '復讐'},
    'age-difference': {'en': 'Age Gap', 'zh': '年齡差', 'zh-Hans': '年龄差', 'ja': '年の差'},
    'giant': {'en': 'Giant Man', 'zh': '巨漢', 'zh-Hans': '巨汉', 'ja': '巨漢'},
    'love-potion': {'en': 'Aphrodisiac', 'zh': '媚藥', 'zh-Hans': '媚药', 'ja': '媚薬'},
    'sex-beside-husband': {'en': 'In Front of Husband', 'zh': '夫目前犯', 'zh-Hans': '夫目前犯', 'ja': '夫の目の前'},
    'affair': {'en': 'Affair', 'zh': '出軌', 'zh-Hans': '出轨', 'ja': '不倫'},
    'hypnosis': {'en': 'Hypnosis', 'zh': '催眠', 'zh-Hans': '催眠', 'ja': '催眠'},
    'private-cam': {'en': 'Voyeur', 'zh': '偷拍', 'zh-Hans': '偷拍', 'ja': '盗撮'},
    'rainy-day': {'en': 'Rainy Day', 'zh': '下雨天', 'zh-Hans': '下雨天', 'ja': '雨の日'},
    'ntr': {'en': 'NTR', 'zh': 'NTR', 'zh-Hans': 'NTR', 'ja': 'NTR'},
    'club-hostess-and-sex-worker': {'en': 'Hostess/Sex Worker', 'zh': '風俗娘', 'zh-Hans': '风俗娘', 'ja': '風俗嬢'},
    'doctor': {'en': 'Doctor', 'zh': '醫生', 'zh-Hans': '医生', 'ja': '医師'},
    'fugitive': {'en': 'Fugitive', 'zh': '逃犯', 'zh-Hans': '逃犯', 'ja': '逃亡者'},
    'nurse': {'en': 'Nurse', 'zh': '護士', 'zh-Hans': '护士', 'ja': 'ナース'},
    'teacher': {'en': 'Teacher', 'zh': '老師', 'zh-Hans': '老师', 'ja': '教師'},
    'flight-attendant': {'en': 'Flight Attendant', 'zh': '空姐', 'zh-Hans': '空姐', 'ja': 'CA'},
    'team-manager': {'en': 'Team Manager', 'zh': '球隊經理', 'zh-Hans': '球队经理', 'ja': 'マネージャー'},
    'widow': {'en': 'Widow', 'zh': '未亡人', 'zh-Hans': '未亡人', 'ja': '未亡人'},
    'detective': {'en': 'Investigator', 'zh': '搜查官', 'zh-Hans': '搜查官', 'ja': '捜査官'},
    'couple': {'en': 'Couple', 'zh': '情侶', 'zh-Hans': '情侣', 'ja': 'カップル'},
    'housewife': {'en': 'Housekeeper', 'zh': '家政婦', 'zh-Hans': '家政妇', 'ja': '家政婦'},
    'private-teacher': {'en': 'Tutor', 'zh': '家庭教師', 'zh-Hans': '家庭教师', 'ja': '家庭教師'},
    'idol': {'en': 'Idol', 'zh': '偶像', 'zh-Hans': '偶像', 'ja': 'アイドル'},
    'wife': {'en': 'Married Woman', 'zh': '人妻', 'zh-Hans': '人妻', 'ja': '人妻'},
    'female-anchor': {'en': 'Female Announcer', 'zh': '主播', 'zh-Hans': '主播', 'ja': '女子アナ'},
    'ol': {'en': 'OL', 'zh': 'OL', 'zh-Hans': 'OL', 'ja': 'OL'},
    'magic-mirror': {'en': 'Magic Mirror Van', 'zh': '魔鏡號', 'zh-Hans': '魔镜号', 'ja': 'マジックミラー号'},
    'tram': {'en': 'Train', 'zh': '電車', 'zh-Hans': '电车', 'ja': '電車'},
    'first-night': {'en': 'Virgin', 'zh': '處女', 'zh-Hans': '处女', 'ja': '処女'},
    'prison': {'en': 'Prison', 'zh': '監獄', 'zh-Hans': '监狱', 'ja': '監獄'},
    'hot-spring': {'en': 'Hot Spring', 'zh': '溫泉', 'zh-Hans': '温泉', 'ja': '温泉'},
    'bathing-place': {'en': 'Bathhouse', 'zh': '洗浴場', 'zh-Hans': '洗浴场', 'ja': '浴場'},
    'swimming-pool': {'en': 'Pool', 'zh': '泳池', 'zh-Hans': '泳池', 'ja': 'プール'},
    'car': {'en': 'Car', 'zh': '汽車', 'zh-Hans': '汽车', 'ja': '車'},
    'toilet': {'en': 'Toilet', 'zh': '廁所', 'zh-Hans': '厕所', 'ja': 'トイレ'},
    'school': {'en': 'School', 'zh': '學校', 'zh-Hans': '学校', 'ja': '学校'},
    'library': {'en': 'Library', 'zh': '圖書館', 'zh-Hans': '图书馆', 'ja': '図書館'},
    'gym-room': {'en': 'Gym', 'zh': '健身房', 'zh-Hans': '健身房', 'ja': 'ジム'},
    'store': {'en': 'Convenience Store', 'zh': '便利店', 'zh-Hans': '便利店', 'ja': 'コンビニ'},
    'video-recording': {'en': 'Filming', 'zh': '錄像', 'zh-Hans': '录像', 'ja': 'ビデオ撮影'},
    'debut-retires': {'en': 'Debut / Retirement', 'zh': '處女作/引退作', 'zh-Hans': '处女作/引退作', 'ja': 'デビュー・引退作'},
    'variety-show': {'en': 'Variety Show', 'zh': '綜藝', 'zh-Hans': '综艺', 'ja': 'バラエティ'},
    'festival': {'en': 'Holiday Theme', 'zh': '節日主題', 'zh-Hans': '节日主题', 'ja': 'イベントもの'},
    'thanksgiving': {'en': 'Fan Appreciation', 'zh': '感謝祭', 'zh-Hans': '感谢祭', 'ja': '感謝祭'},
    'more-than-4-hours': {'en': 'Over 4 Hours', 'zh': '4小時以上', 'zh-Hans': '4小时以上', 'ja': '4時間以上'},
}


CATEGORY_I18N = {
    'https://huangguoai.com/ai-duanju/': {
        'en': 'AI Short Dramas', 'zh': 'AI 短劇', 'zh-Hans': 'AI 短剧',
        'ja': 'AI ショートドラマ', 'th': 'ละครสั้น AI'},
    'https://huangguoai.com/ai-manju/': {
        'en': 'AI Animated Dramas', 'zh': 'AI 漫劇', 'zh-Hans': 'AI 漫剧',
        'ja': 'AI アニメ', 'th': 'แอนิเมชัน AI'},
    'https://jable.tv/latest-updates/': {'en': 'Latest', 'zh': '最近更新', 'zh-Hans': '最近更新', 'ja': '最新更新'},
    'https://jable.tv/hot/': {'en': 'Popular', 'zh': '熱門影片', 'zh-Hans': '热门视频', 'ja': '人気動画'},
    'https://jable.tv/new-release/': {'en': 'New', 'zh': '新片上架', 'zh-Hans': '新片上架', 'ja': '新作'},
    'https://missav.ai/dm296/today-hot': {'en': "Today's Hot", 'zh': '今日熱門', 'zh-Hans': '今日热门', 'ja': '今日の人気'},
    'https://missav.ai/dm298/today-hot': {'en': "Today's Hot", 'zh': '今日熱門', 'zh-Hans': '今日热门', 'ja': '今日の人気'},
    'https://missav.ai/dm170/weekly-hot': {'en': 'Weekly Hot', 'zh': '本週熱門', 'zh-Hans': '本周热门', 'ja': '今週の人気'},
    'https://missav.ai/dm266/monthly-hot': {'en': 'Monthly Hot', 'zh': '本月熱門', 'zh-Hans': '本月热门', 'ja': '今月の人気'},
    'https://missav.ai/dm270/monthly-hot': {'en': 'Monthly Hot', 'zh': '本月熱門', 'zh-Hans': '本月热门', 'ja': '今月の人気'},
    'https://missav.ai/dm278/chinese-subtitle': {'en': 'Chinese Subtitles', 'zh': '中文字幕', 'zh-Hans': '中文字幕', 'ja': '中国語字幕'},
    'https://missav.ai/dm539/new': {'en': 'Latest', 'zh': '最近更新', 'zh-Hans': '最近更新', 'ja': '最新更新'},
    'https://missav.ai/dm632/release': {'en': 'New Releases', 'zh': '新作上市', 'zh-Hans': '新作上市', 'ja': '新作'},
    'https://missav.ai/dm634/release': {'en': 'New Releases', 'zh': '新作上市', 'zh-Hans': '新作上市', 'ja': '新作'},
    'https://missav.ai/dm816/uncensored-leak': {'en': 'Uncensored Leaks', 'zh': '無碼流出', 'zh-Hans': '无码流出', 'ja': '無修正流出'},
    'https://missav.ai/dm817/uncensored-leak': {'en': 'Uncensored Leaks', 'zh': '無碼流出', 'zh-Hans': '无码流出', 'ja': '無修正流出'},
    'https://missav.ai/dm36/siro': {'en': 'SIRO', 'zh': 'SIRO', 'zh-Hans': 'SIRO', 'ja': 'SIRO'},
    'https://missav.ai/dm473/fc2': {'en': 'FC2', 'zh': 'FC2', 'zh-Hans': 'FC2', 'ja': 'FC2'},
    'https://missav.ai/dm541/fc2': {'en': 'FC2', 'zh': 'FC2', 'zh-Hans': 'FC2', 'ja': 'FC2'},
    'https://missav.ai/dm63/madou': {'en': 'Madou Media', 'zh': '麻豆傳媒', 'zh-Hans': '麻豆传媒', 'ja': '麻豆傳媒'},
    'https://missav.ai/dm42/tokyohot': {'en': 'Tokyo Hot', 'zh': '東京熱', 'zh-Hans': '东京热', 'ja': '東京熱'},
    'https://missav.ai/dm4286298/1pondo': {'en': '1Pondo', 'zh': '一本道', 'zh-Hans': '一本道', 'ja': '一本道'},
    'https://missav.ai/dm4854130/1pondo': {'en': '1Pondo', 'zh': '一本道', 'zh-Hans': '一本道', 'ja': '一本道'},
    'https://supjav.com/': {'en': 'Latest', 'zh': '最近更新', 'zh-Hans': '最近更新', 'ja': '最新更新'},
    'https://supjav.com/popular': {'en': 'Popular', 'zh': '熱門總榜', 'zh-Hans': '热门总榜', 'ja': '人気総合'},
    'https://supjav.com/popular?sort=week': {'en': 'Weekly Hot', 'zh': '本週熱門', 'zh-Hans': '本周热门', 'ja': '今週の人気'},
    'https://supjav.com/popular?sort=month': {'en': 'Monthly Hot', 'zh': '本月熱門', 'zh-Hans': '本月热门', 'ja': '今月の人気'},
    'https://supjav.com/category/uncensored-jav': {'en': 'Uncensored', 'zh': '無碼', 'zh-Hans': '无码', 'ja': '無修正'},
    'https://supjav.com/category/censored-jav': {'en': 'Censored', 'zh': '有碼', 'zh-Hans': '有码', 'ja': '有修正'},
    'https://supjav.com/category/amateur': {'en': 'Amateur', 'zh': '素人', 'zh-Hans': '素人', 'ja': '素人'},
    'https://supjav.com/category/chinese-subtitles': {'en': 'Chinese Subtitles', 'zh': '中文字幕', 'zh-Hans': '中文字幕', 'ja': '中国語字幕'},
    'https://supjav.com/category/english-subtitles': {'en': 'English Subtitles', 'zh': '英文字幕', 'zh-Hans': '英文字幕', 'ja': '英語字幕'},
    'https://supjav.com/category/reducing-mosaic': {'en': 'Mosaic Removed', 'zh': '破壞版', 'zh-Hans': '破坏版', 'ja': 'モザイク破壊版'},
}


def _hanime1_filter_url(key, value):
    return 'https://hanime1.me/search?' + urlencode([(key, value)])


_HANIME1_LABELS = {
    ('sort', '最新上市'): {
        'en': 'Latest Releases', 'zh': '最新上市',
        'zh-Hans': '最新上市', 'ja': '新着発売'},
    ('sort', '最新上傳'): {
        'en': 'Latest Uploads', 'zh': '最新上傳',
        'zh-Hans': '最新上传', 'ja': '最新アップロード'},
    ('sort', '本日排行'): {
        'en': 'Daily Ranking', 'zh': '本日排行',
        'zh-Hans': '今日排行', 'ja': 'デイリーランキング'},
    ('sort', '本週排行'): {
        'en': 'Weekly Ranking', 'zh': '本週排行',
        'zh-Hans': '本周排行', 'ja': '週間ランキング'},
    ('sort', '本月排行'): {
        'en': 'Monthly Ranking', 'zh': '本月排行',
        'zh-Hans': '本月排行', 'ja': '月間ランキング'},
    ('sort', '觀看次數'): {
        'en': 'Most Viewed', 'zh': '觀看次數',
        'zh-Hans': '观看次数', 'ja': '再生回数'},
    ('sort', '讚好比例'): {
        'en': 'Top Rated', 'zh': '讚好比例',
        'zh-Hans': '好评率', 'ja': '高評価率'},
    ('sort', '時長最長'): {
        'en': 'Longest', 'zh': '時長最長',
        'zh-Hans': '时长最长', 'ja': '長時間順'},
    ('sort', '他們在看'): {
        'en': 'Watching Now', 'zh': '他們在看',
        'zh-Hans': '他们在看', 'ja': '視聴中'},
    ('genre', '裏番'): {
        'en': 'Hentai Anime', 'zh': '裏番',
        'zh-Hans': '里番', 'ja': '裏アニメ'},
    ('genre', '泡麵番'): {
        'en': 'Short Anime', 'zh': '泡麵番',
        'zh-Hans': '泡面番', 'ja': 'ショートアニメ'},
    ('genre', 'Motion Anime'): {
        'en': 'Motion Anime', 'zh': 'Motion Anime',
        'zh-Hans': 'Motion Anime', 'ja': 'モーションアニメ'},
    ('genre', '3DCG'): {
        'en': '3DCG', 'zh': '3DCG', 'zh-Hans': '3DCG', 'ja': '3DCG'},
    ('genre', '2.5D'): {
        'en': '2.5D', 'zh': '2.5D', 'zh-Hans': '2.5D', 'ja': '2.5D'},
    ('genre', '2D動畫'): {
        'en': '2D Animation', 'zh': '2D動畫',
        'zh-Hans': '2D动画', 'ja': '2Dアニメ'},
    ('genre', 'AI生成'): {
        'en': 'AI Generated', 'zh': 'AI生成',
        'zh-Hans': 'AI生成', 'ja': 'AI生成'},
    ('genre', 'MMD'): {
        'en': 'MMD', 'zh': 'MMD', 'zh-Hans': 'MMD', 'ja': 'MMD'},
    ('genre', 'Cosplay'): {
        'en': 'Cosplay', 'zh': 'Cosplay',
        'zh-Hans': 'Cosplay', 'ja': 'コスプレ'},
    ('tags[]', '中文字幕'): {
        'en': 'Chinese Subtitles', 'zh': '中文字幕',
        'zh-Hans': '中文字幕', 'ja': '中国語字幕'},
    ('tags[]', '中文配音'): {
        'en': 'Chinese Dub', 'zh': '中文配音',
        'zh-Hans': '中文配音', 'ja': '中国語吹替'},
    ('tags[]', '無碼'): {
        'en': 'Uncensored', 'zh': '無碼',
        'zh-Hans': '无码', 'ja': '無修正'},
    ('tags[]', 'AI解碼'): {
        'en': 'AI Decoded', 'zh': 'AI解碼',
        'zh-Hans': 'AI解码', 'ja': 'AIデコード'},
    ('tags[]', '1080p'): {
        'en': '1080p', 'zh': '1080p', 'zh-Hans': '1080p', 'ja': '1080p'},
    ('tags[]', '60FPS'): {
        'en': '60 FPS', 'zh': '60 FPS', 'zh-Hans': '60 FPS', 'ja': '60 FPS'},
}

for (_key, _value), _labels in _HANIME1_LABELS.items():
    CATEGORY_I18N[_hanime1_filter_url(_key, _value)] = _labels


def loc(table, key, fallback=''):
    entry = table.get(key)
    if not entry:
        return fallback or key
    return entry.get(get_lang()) or entry.get('en') or fallback or key
