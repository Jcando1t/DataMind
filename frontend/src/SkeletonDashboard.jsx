import { Skeleton, Row, Col, Card } from 'antd';
import './SkeletonDashboard.css';

export default function SkeletonDashboard() {
  return (
    <div className="skeleton-dashboard">
      {/* Overview Metrics - 4 Cards */}
      <Row gutter={[16, 16]} className="metrics-row">
        {Array(4)
          .fill(null)
          .map((_, idx) => (
            <Col xs={24} sm={12} lg={6} key={idx}>
              <Card className="skeleton-card">
                <Skeleton active paragraph={{ rows: 2 }} />
              </Card>
            </Col>
          ))}
      </Row>

      {/* Trend Chart */}
      <Row gutter={[16, 16]} className="chart-row">
        <Col xs={24} lg={14}>
          <Card className="skeleton-card">
            <Skeleton active paragraph={{ rows: 8 }} />
          </Card>
        </Col>

        {/* Category Rank Table */}
        <Col xs={24} lg={10}>
          <Card className="skeleton-card">
            <Skeleton active paragraph={{ rows: 8 }} />
          </Card>
        </Col>
      </Row>

      {/* Top Sellers Table */}
      <Row gutter={[16, 16]} className="table-row">
        <Col xs={24}>
          <Card className="skeleton-card">
            <Skeleton active paragraph={{ rows: 6 }} />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
