const ICP_RECORD_NUMBER = '晋ICP备2026005159号-4';
const ICP_RECORD_URL = 'https://beian.miit.gov.cn/';

export default function ComplianceFooter() {
  return (
    <footer className="compliance-footer" aria-label="网站备案信息">
      <a
        className="compliance-footer__link"
        href={ICP_RECORD_URL}
        target="_blank"
        rel="noopener noreferrer"
      >
        {ICP_RECORD_NUMBER}
      </a>
    </footer>
  );
}
